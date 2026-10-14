"""Opt-in, current-Windows-user session vault. Never persists passwords.

DPAPI without LOCAL_MACHINE; UI forbidden. No plaintext fallback on other OSes.
Only our own session is protected, not cookies read from a browser profile.
"""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import tempfile
import time


def _dpapi(data, decrypt=False):
    if os.name != 'nt':
        raise OSError('保持登录仅支持 Windows 当前用户加密存储')
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buf = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


class LoginMemory:
    TTL = 30 * 86400

    def __init__(self, data, store, protect=None, unprotect=None, clock=time.time):
        self.path = Path(data) / 'login-session.dpapi'
        self.store = store
        self.protect = protect or _dpapi
        self.unprotect = unprotect or (lambda b: _dpapi(b, True))
        self.clock = clock

    def preferences(self):
        return {'remember_account': self.store.get_kv('login_remember', '1') == '1',
                'keep_signed_in': self.store.get_kv('login_keep', '0') == '1'}

    def forget(self):
        self.path.unlink(missing_ok=True)

    def completed(self, client, options, account='', kind='pw'):
        """Call only after successful authentication; failures don't replace a vault."""
        prefs = self.preferences()
        prefs = {k: options.get(k, prefs[k]) is True for k in prefs}
        # Never leave a previous user's restorable session after a new login.
        self.forget()
        self.store.set_many({'login_remember': '1' if prefs['remember_account'] else '0',
                             'login_keep': '1' if prefs['keep_signed_in'] else '0'})
        if account:
            if prefs['remember_account']:
                self.store.save_account(account, kind)
            else:
                self.store.del_account(account)
        if not prefs['keep_signed_in']:
            return ''
        try:
            now = self.clock()
            raw = json.dumps({'version': 1, 'created': now, 'expires': now + self.TTL,
                              'cookies': dict(client.cookies)}, ensure_ascii=False).encode('utf-8')
            encrypted = self.protect(raw)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix='.login-', dir=self.path.parent)
            try:
                with os.fdopen(fd, 'wb') as f:
                    f.write(encrypted)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(name, self.path)
            finally:
                Path(name).unlink(missing_ok=True)
            return ''
        except Exception:
            self.store.set_kv('login_keep', '0')
            return '登录成功，但本机无法加密保存登录状态；本次退出后需重新登录。'

    def read(self):
        if not self.preferences()['keep_signed_in']:
            self.forget()
            return None
        if not self.path.exists():
            return None
        try:
            if self.path.stat().st_size > 1024 * 1024:
                raise ValueError('oversize')
            value = json.loads(self.unprotect(self.path.read_bytes()).decode('utf-8'))
            now = self.clock()
            if (value['version'] != 1 or not 0 <= now - value['created'] <= self.TTL
                    or not now < value['expires'] <= value['created'] + self.TTL):
                raise ValueError('expired')
            cookies = value['cookies']
            if (not isinstance(cookies, dict) or not cookies.get('u_id') or not cookies.get('u_co_id')
                    or any(not isinstance(k, str) or not isinstance(v, str) for k, v in cookies.items())):
                raise ValueError('invalid session')
            return cookies
        except Exception:
            self.forget()
            return None

    def restore(self, client, factory):
        if client.uid:
            return {'restored': True}
        cookies = self.read()
        if not cookies:
            return {'restored': False}
        # Validate with a separate read-only client before exposing a logged-in state.
        probe = factory()
        try:
            probe.adopt_cookies(cookies)
            probe._viewstate(probe._list_url(probe.coid, probe.coid))
        except Exception as exc:
            if '登录态失效' in str(exc):
                self.forget()
                return {'restored': False, 'msg': '保存的登录已失效，请重新登录。'}
            # A transient network/permission failure must not destroy the saved token.
            return {'restored': False, 'msg': '暂时无法验证保存的登录，请检查网络后重试，或手动登录。'}
        client.adopt_cookies(probe.cookies)
        return {'restored': True}
