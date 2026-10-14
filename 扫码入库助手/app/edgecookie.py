# -*- coding: utf-8 -*-
"""
从 Edge 的**独立配置目录**里把 Cookies 读出来（Windows）。

只用标准库：sqlite3 读库、ctypes 调 CryptUnprotectData 解密 DPAPI。

为什么必须用独立配置目录：
  Edge 主进程运行时会用独占句柄锁住 Cookies 数据库，直接复制/直连都会 PermissionError。
  扫码登录时由本程序自己用 --user-data-dir 起一个专用 Profile，窗口也由本程序管理，
  取 Cookie 前先把这个窗口整棵进程树杀掉，句柄释放后就能正常读。

已知限制：
  Chromium 新版引入了 App-Bound Encryption，会话 Cookie 可能被写进
  app_bound_encrypted_value 列，此时纯 DPAPI 解不出来 —— 会明确报错让前端降级，
  不会静默返回空。
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import time

IS_WIN = sys.platform.startswith("win")


class CookieError(Exception):
    pass


# ------------------------------------------------------------------ DPAPI

def dpapi_decrypt(blob):
    """用当前 Windows 用户凭据解密。只在 Windows 上可用。"""
    if not IS_WIN:
        raise CookieError("仅支持 Windows")

    import ctypes
    import ctypes.wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(bytes(blob), len(blob))
    inp = DATA_BLOB(len(blob), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(inp), None, None, None, None, 0x01, ctypes.byref(out))
    if not ok:
        raise CookieError("DPAPI 解密失败")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


# ------------------------------------------------------------------ 读取
# Chromium 的 expires_utc 是 1601-01-01 起的微秒；0 表示会话 Cookie（不参与过期判断）
_EPOCH_SHIFT = 11644473600


def _candidate_paths(profile):
    """兼容新版（<profile>/Network/Cookies）与带 Default 子目录的写法"""
    out = [os.path.join(profile, "Network", "Cookies"),
           os.path.join(profile, "Default", "Network", "Cookies"),
           os.path.join(profile, "Cookies")]
    seen, uniq = set(), []
    for p in out:
        k = os.path.normcase(os.path.abspath(p))
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def _open_db(path):
    """先尝试直连只读；Edge 万一还没松手就退化为复制到临时目录再读。
    返回 (连接, 临时文件路径或 None)"""
    try:
        return sqlite3.connect("file:%s?mode=ro&nolock=1" % path.replace("\\", "/"),
                               uri=True, timeout=3), None
    except sqlite3.Error:
        pass
    tmp = os.path.join(tempfile.gettempdir(), "jst_cookies_%d.db" % int(time.time() * 1000))
    try:
        shutil.copyfile(path, tmp)
        return sqlite3.connect(tmp, timeout=3), tmp
    except Exception as e:
        raise CookieError("无法读取浏览器 Cookie 数据库（%s）" % e)


def read_host_cookies(profile, host_suffix="erp321.com"):
    """
    读取指定 Profile 下、域名以 host_suffix 结尾的全部 Cookie。

    返回 dict：cookie 名 -> 明文值。同名冲突时以父域（如 .erp321.com）的为准。
    抛出 CookieError 时会带上可解释给用户的原因。
    """
    profile = os.path.abspath(profile or "")
    if not profile or not os.path.isdir(profile):
        raise CookieError("登录窗口还没有启动过")

    db_path = next((p for p in _candidate_paths(profile) if os.path.isfile(p)), None)
    if not db_path:
        raise CookieError("没有找到浏览器的 Cookie 数据库，请先在弹出的窗口里完成扫码登录")

    con, tmp = _open_db(db_path)
    try:
        rows = _query(con, host_suffix)
    finally:
        try:
            con.close()
        except Exception:
            pass
        if tmp:
            try:
                os.remove(tmp)
            except Exception:
                pass

    out, blocked = {}, []
    for host, name, plain, enc, exp, abl in rows:
        name = name or ""
        if not name:
            continue
        if exp and exp / 1e6 - _EPOCH_SHIFT < time.time() - 60:
            continue                                   # 已过期
        if abl:
            blocked.append(name)
            continue
        val = plain
        if enc:
            enc = bytes(enc)
            # Chromium 的 encrypted_value = "v10"/"v11" 前缀 + DPAPI 密文，
            # 解密前必须把前缀剥掉（Chrome 内部也是这么切的）
            if enc[:3] in (b"v10", b"v11", b"v20"):
                try:
                    val = dpapi_decrypt(enc[3:]).decode("utf-8", "replace")
                except Exception:
                    continue
            else:
                try:
                    val = enc.decode("utf-8", "replace")
                except Exception:
                    continue
        if val in (None, ""):
            continue
        if name not in out:
            out[name] = val

    if not out and blocked:
        raise CookieError(
            "本机 Edge 使用了新版「应用绑定加密」保存 Cookie，无法直接读取。"
            "请改用账号密码或短信验证码登录。")

    return out, blocked


def _query(con, host_suffix):
    try:
        has_ab = bool(con.execute(
            "SELECT 1 FROM pragma_table_info('cookies') WHERE name='app_bound_encrypted_value'"
        ).fetchone())
    except Exception:
        has_ab = False
    ab_expr = "length(coalesce(app_bound_encrypted_value, x''))" if has_ab else "0"
    # 父域(.erp321.com)排前面：同名冲突时以覆盖面更广的为准
    return con.execute(
        "SELECT host_key, name, value, encrypted_value, expires_utc, %s AS abl "
        "FROM cookies WHERE lower(host_key) LIKE ? "
        "ORDER BY (substr(host_key,1,1)=='.') DESC, length(host_key) ASC"
        % ab_expr, ("%" + host_suffix.lower(),)).fetchall()
