"""Local Windows rich clipboard: one text fragment + local PNG, no sending.
Only desktop loopback HTTP exposes this. No ERP, database or chat access.
"""
import base64
import binascii
import ctypes
import hashlib
import html
import os
from pathlib import Path
import re
import secrets
import struct
import threading
import time
import uuid
import zlib

MAX_REQUEST_BYTES = 12 * 1024 * 1024
MAX_PNG_BYTES = 8 * 1024 * 1024
MAX_TEXT_CHARS = 16000
PNG_SIGNATURE = bytes.fromhex('89504e470d0a1a0a')


def cf_html(fragment):
    prefix = '<html><body><!--StartFragment-->'
    suffix = '<!--EndFragment--></body></html>'
    template = ('Version:1.0\r\nStartHTML:{:010d}\r\nEndHTML:{:010d}\r\n'
                'StartFragment:{:010d}\r\nEndFragment:{:010d}\r\n')
    size = len(template.format(0, 0, 0, 0).encode('ascii'))
    body = (prefix + fragment + suffix).encode('utf-8')
    start = size + len(prefix.encode('utf-8'))
    end = start + len(fragment.encode('utf-8'))
    return template.format(size, size + len(body), start, end).encode('ascii') + body + b'\0'


def validate_png(data):
    if len(data) > MAX_PNG_BYTES or not data.startswith(PNG_SIGNATURE):
        raise ValueError('结果图片不是有效PNG，或超过8MB限制')
    offset, dimensions, saw_data, ended = 8, None, False, False
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError('PNG图片不完整，未复制')
        size = struct.unpack_from('>I', data, offset)[0]
        kind = data[offset + 4:offset + 8]
        end = offset + size + 12
        if end > len(data):
            raise ValueError('PNG图片不完整，未复制')
        chunk = data[offset + 8:end - 4]
        crc = struct.unpack_from('>I', data, end - 4)[0]
        if zlib.crc32(kind + chunk) & 0xffffffff != crc:
            raise ValueError('PNG校验失败，未复制')
        if dimensions is None:
            if kind != b'IHDR' or size != 13:
                raise ValueError('PNG缺少有效尺寸信息')
            width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', chunk)
            if not (0 < width <= 4096 and 0 < height <= 30000 and width * height <= 40000000):
                raise ValueError('结果图片尺寸超限，未截断或复制')
            if depth != 8 or color not in (2, 6) or compression or filtering or interlace:
                raise ValueError('不支持此PNG格式，请重新生成报告')
            dimensions = (width, height)
        elif kind == b'IHDR':
            raise ValueError('PNG重复图片头')
        if kind == b'IDAT':
            saw_data = True
        if kind == b'IEND':
            if size or end != len(data) or not saw_data:
                raise ValueError('PNG图片不完整或包含尾随数据')
            ended = True
            break
        offset = end
    if not ended:
        raise ValueError('PNG图片不完整，未复制')
    return dimensions


def write_windows_clipboard(html_bytes, text):
    """Use standard CF_HTML, not an alternate bitmap that hides the text."""
    if os.name != 'nt':
        raise OSError('微信图文复制仅支持运行助手的Windows电脑')
    from ctypes import wintypes as w
    u = ctypes.WinDLL('user32', use_last_error=True)
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    u.OpenClipboard.argtypes = [w.HWND]; u.OpenClipboard.restype = w.BOOL
    u.EmptyClipboard.restype = w.BOOL
    u.CloseClipboard.restype = w.BOOL
    u.SetClipboardData.argtypes = [w.UINT, w.HANDLE]; u.SetClipboardData.restype = w.HANDLE
    u.RegisterClipboardFormatW.argtypes = [w.LPCWSTR]; u.RegisterClipboardFormatW.restype = w.UINT
    k.GlobalAlloc.argtypes = [w.UINT, ctypes.c_size_t]; k.GlobalAlloc.restype = w.HGLOBAL
    k.GlobalLock.argtypes = [w.HGLOBAL]; k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalUnlock.argtypes = [w.HGLOBAL]; k.GlobalUnlock.restype = w.BOOL
    k.GlobalFree.argtypes = [w.HGLOBAL]; k.GlobalFree.restype = w.HGLOBAL
    fmt = u.RegisterClipboardFormatW('HTML Format')
    if not fmt:
        raise OSError('无法注册图文剪贴板格式')
    blocks = []
    try:
        # Allocate everything before clearing the clipboard; don't lose it on allocation errors.
        for kind, data in [(fmt, html_bytes), (13, (text + '\0').encode('utf-16-le'))]:
            handle = k.GlobalAlloc(2, len(data))
            if not handle:
                raise OSError('剪贴板内存分配失败')
            blocks.append([kind, handle])
            pointer = k.GlobalLock(handle)
            if not pointer:
                raise OSError('剪贴板内存锁定失败')
            ctypes.memmove(pointer, data, len(data)); k.GlobalUnlock(handle)
        for attempt in range(10):
            if u.OpenClipboard(None):
                break
            time.sleep(0.05)
        else:
            raise OSError('剪贴板正被其他程序占用，请稍后重试')
        try:
            if not u.EmptyClipboard():
                raise OSError('无法更新剪贴板，请重试')
            for block in blocks:
                if not u.SetClipboardData(block[0], block[1]):
                    # A partially published set must not be mistaken for a successful copy.
                    u.EmptyClipboard()
                    raise OSError('图文未完整写入剪贴板，请重新复制')
                block[1] = None  # ownership transferred to Windows
        finally:
            u.CloseClipboard()
    finally:
        for kind, handle in blocks:
            if handle:
                k.GlobalFree(handle)


class ReportClipboard:
    def __init__(self, directory, writer=None):
        self.directory = Path(directory).resolve()
        self.writer = writer or write_windows_clipboard
        self.supported = os.name == 'nt'
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()

    def status(self):
        return {'supported': self.supported, 'transport': 'windows-cf-html',
                'token': self.token if self.supported else ''}

    def copy(self, payload):
        if not self.supported:
            raise ValueError('本机不支持Windows图文剪贴板')
        if not isinstance(payload, dict) or not isinstance(payload.get('token'), str) or not secrets.compare_digest(payload['token'], self.token):
            raise PermissionError('复制凭据已失效，请重试或刷新页面')
        text, encoded = payload.get('text'), payload.get('png')
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS or '\0' in text:
            raise ValueError('单号文字为空或超长，未复制')
        prefix = 'data:image/png;base64,'
        if not isinstance(encoded, str) or not encoded.startswith(prefix) or len(encoded) > MAX_REQUEST_BYTES:
            raise ValueError('结果图片格式不正确或超限，未复制')
        try:
            png = base64.b64decode(encoded[len(prefix):], validate=True)
        except (ValueError, binascii.Error):
            raise ValueError('结果图片编码不正确，未复制') from None
        width, height = validate_png(png)
        with self.lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / ('report-' + uuid.uuid4().hex + '.png')
            # Unique file is immutable so repeated copies cannot change an older draft image.
            with path.open('xb') as output:
                output.write(png)
            fragment = '<div>' + html.escape(text).replace('\n', '<br>') + '</div><div><img src="' + path.as_uri() + '" width="' + str(width) + '" height="' + str(height) + '"></div>'
            try:
                self.writer(cf_html(fragment), text)
            except Exception:
                # Keep the file even after a reported write error: partial clipboard ownership is possible.
                raise
            self._prune(path)
        return {'transport': 'windows-cf-html', 'width': width, 'height': height,
                'sha256': hashlib.sha256(png).hexdigest()}

    def _prune(self, current):
        # Only our immutable PNGs older than 7 days; never arbitrary paths or user exports.
        cutoff = time.time() - 7 * 86400
        for path in self.directory.glob('report-*.png'):
            try:
                if path != current and re.fullmatch(r'report-[0-9a-f]{32}\.png', path.name) and not path.is_symlink() and path.resolve().parent == self.directory and path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass  # cache cleanup must not turn a successful copy into a reported failure
