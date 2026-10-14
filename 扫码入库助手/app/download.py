"""Opt-in, expiring LAN installer server. Serves only the bundled signed APK; no ERP APIs."""
import base64
import hashlib
import html
import json
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def qr_image(text):
    import qrcode
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
    qr.add_data(text)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    size = len(matrix)
    path = " ".join("M%d,%dh1v1h-1z" % (x, y) for y, row in enumerate(matrix) for x, on in enumerate(row) if on)
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" shape-rendering="crispEdges"><rect width="100%%" height="100%%" fill="white"/><path d="%s" fill="black"/></svg>' % (size, size, path)
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


class InstallerHost:
    def __init__(self, app, directory):
        self.app, self.directory = app, Path(directory)
        self.server = None
        self.expires = 0
        self.url = ""
        self.qr = ""
        self.timer = None
        self.lock = threading.RLock()

    def metadata(self):
        try:
            info = json.loads((self.directory / "manifest.json").read_text(encoding="utf-8"))
            data = (self.directory / "pda.apk").read_bytes()
            if not isinstance(info, dict) or not isinstance(info.get("version"), str) or not data:
                raise ValueError("安装包清单格式错误，请重新解压完整发布包")
            if hashlib.sha256(data).hexdigest() != info["sha256"]:
                raise ValueError("安装包校验失败，请重新解压完整发布包")
            return info, data
        except (OSError, KeyError, json.JSONDecodeError):
            raise ValueError("未找到随程序发布的PDA安装包，请使用完整的2.0.5或更新版电脑端")

    def status(self):
        with self.lock:
            enabled = bool(self.server and time.time() < self.expires)
            try:
                info, _ = self.metadata()
                error = ""
            except ValueError as e:
                info, error = {}, str(e)
            return {"enabled": enabled, "addresses": self.app.LAN.addresses(),
                    "url": self.url if enabled else "", "qr": self.qr if enabled else "",
                    "expires": self.expires if enabled else 0, "package": info, "error": error}

    def start(self, host, port=8767):
        with self.lock:
            if self.server:
                raise ValueError("下载服务已开启，请先关闭再更换网卡或端口")
            if host not in self.app.LAN.addresses() and host != "127.0.0.1":
                raise ValueError("请选择本机局域网IPv4地址")
            port = int(port)
            if not 1024 <= port <= 65535:
                raise ValueError("端口需为1024到65535")
            info, data = self.metadata()
            token = secrets.token_urlsafe(24)
            page_path = "/d/" + token
            apk_path = page_path + "/pda.apk"
            # Prepare QR before binding: an unavailable encoder must not leak a socket.
            url = "http://%s:%d%s" % (host, port, page_path)
            qr = self._make_qr(url)
            owner = self
            class Handler(BaseHTTPRequestHandler):
                def setup(self):
                    super().setup()
                    self.connection.settimeout(10)
                def log_message(self, *_):
                    pass  # no URLs, request headers or tokens in logs
                def send(self, status, body, mime, extra=None, head=False):
                    self.send_response(status)
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; form-action 'none'; base-uri 'none'")
                    for k, v in (extra or {}).items():
                        self.send_header(k, v)
                    self.end_headers()
                    if not head:
                        self.wfile.write(body)
                def do_POST(self):
                    self.close_connection = True
                    self.send(405, b"Download only", "text/plain")
                def do_HEAD(self):
                    self.do_GET(head=True)
                def do_GET(self, head=False):
                    if time.time() >= owner.expires:
                        return self.send(410, b"Download link expired", "text/plain", head=head)
                    if self.headers.get("Host") != "%s:%d" % (host, self.server.server_port):
                        return self.send(403, b"Invalid host", "text/plain", head=head)
                    if self.path == page_path:
                        page = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                                '<title>PDA安装包下载</title><body style="font-family:sans-serif;padding:20px;word-break:break-all">'
                                '<h1>扫码入库 PDA %s</h1><p>支持 Android 6.0 及以上。此码仅下载安装包，不是设备配对码。</p>'
                                '<p><a style="display:block;padding:20px;background:#1f6feb;color:white;text-align:center" href="%s" download="receipt-pda.apk">下载 APK 安装包</a></p>'
                                '<p>如果系统询问，请允许本次安装来源；安装完成后可关闭该权限。安装后到电脑设置生成另一个“配对二维码”。</p>'
                                '<p>仅在可信局域网下载。SHA256请与电脑核对：</p><code>%s</code><p>10分钟后链接失效，下载不了请在电脑重新生成。请勿将端口开放到公网。</p></body></html>') % (html.escape(str(info["version"])), apk_path, info["sha256"])
                        return self.send(200, page.encode(), "text/html; charset=utf-8", head=head)
                    if self.path != apk_path:
                        return self.send(404, b"Not found", "text/plain", head=head)
                    headers = {"Content-Disposition": 'attachment; filename="receipt-pda.apk"', "Accept-Ranges": "bytes"}
                    selected, status = data, 200
                    value = self.headers.get("Range")
                    if value:
                        match = re.fullmatch(r"bytes=(\d+)-(\d*)", value)
                        if not match:
                            return self.send(416, b"Invalid range", "text/plain", {"Content-Range": "bytes */%d" % len(data)}, head)
                        start = int(match[1]); end = min(int(match[2]) if match[2] else len(data)-1, len(data)-1)
                        if start > end:
                            return self.send(416, b"Invalid range", "text/plain", {"Content-Range": "bytes */%d" % len(data)}, head)
                        selected, status = data[start:end+1], 206
                        headers["Content-Range"] = "bytes %d-%d/%d" % (start, end, len(data))
                    self.send(status, selected, "application/vnd.android.package-archive", headers, head)
            class Server(ThreadingHTTPServer):
                daemon_threads = True
            try:
                server = Server((host, port), Handler)
            except OSError:
                raise ValueError("下载端口被占用或网卡不可用，请更换端口/网卡后重试")
            self.server = server
            self.expires = time.time() + 600
            self.url = url
            self.qr = qr
            threading.Thread(target=server.serve_forever, daemon=True, name="installer-http").start()
            self.timer = threading.Timer(600, lambda: self.stop(expected=server))
            self.timer.daemon = True
            self.timer.start()
            return self.status()

    _make_qr = staticmethod(qr_image)

    def stop(self, expected=None):
        with self.lock:
            if expected is not None and self.server is not expected:
                return
            server, self.server = self.server, None
            self.expires, self.url, self.qr = 0, "", ""
            if self.timer:
                self.timer.cancel()
                self.timer = None
            if server:
                server.shutdown()
                server.server_close()
