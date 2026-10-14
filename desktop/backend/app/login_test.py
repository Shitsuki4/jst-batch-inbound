# -*- coding: utf-8 -*-
"""
登录链路离线测试：三种登录方式全流程 + 各自的异常分支。

不连聚水潭、不起 Edge —— 在测试进程里直接起服务端，
把 JST 客户端的登录方法和 Edge Cookie 读取替换成打桩实现，
验证的是「路由 → 状态机 → 用户看到的文案」这一段自己的逻辑。

用法：python app/login_test.py
"""
import ctypes
import ctypes.wintypes
import json
import os
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

TMP = tempfile.mkdtemp(prefix="jst-logintest")
os.environ["JST_DB_DIR"] = os.path.join(TMP, "data")
os.environ["JST_NO_WATCHDOG"] = "1"
os.environ.setdefault("JST_PORT", "8802")

import server  # noqa: E402
from jst import CaptchaError, LoginError  # noqa: E402

USES = {"captcha_img": 0, "login_args": [], "sms_send_args": [], "sms_login_args": []}


# ------------------------------------------------------------ 打桩
def fake_login(account, password, verify_code="", jd3="", vd144=""):
    USES["login_args"].append((account, password, verify_code))
    if verify_code == "9999":
        return {"uid": "9001", "coid": "70001", "uname": "仓库小李", "coname": "测试公司"}
    if account == "needcaptcha":
        raise CaptchaError("为了您的账号安全，请输入验证码")
    raise LoginError("账号或密码错误")


def fake_validcode(account):
    USES["captcha_img"] += 1
    if account == "badcaptcha":
        raise LoginError("图形验证码加载失败（ETIMEDOUT）")
    return (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff\x21\xf9\x04"
            b"\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02"
            b"\x44\x01\x00\x3b"), "image/gif"


def fake_sms_send(mobile, suffix="", jd3="", vd144=""):
    USES["sms_send_args"].append((mobile, suffix))
    if mobile == "13800000000" and not suffix:
        return {"cid": "", "prefix": "138****0000", "need_suffix": True}
    if mobile == "13900000000":
        raise LoginError("该手机号未绑定任何账号")
    return {"cid": "CID-ABC-123", "prefix": "", "need_suffix": False}


def fake_sms_login(mobile, code, cid, suffix="", jd3="", vd144=""):
    USES["sms_login_args"].append((mobile, code, cid, suffix))
    if code == "000000":
        raise LoginError("验证码错误或已失效")
    if mobile == "13900000000":
        raise LoginError("该手机号尚未绑定聚水潭账号，请先用账号密码登录一次并完成绑定")
    return {"uid": "9002", "coid": "70002", "uname": "短信用户", "coname": "测试公司"}


def fake_adopt(ck):
    if not ck.get("u_id"):
        raise LoginError("浏览器 Cookie 里没有登录身份（u_id），可能还没有扫码成功")
    info = {"uid": ck.get("u_id"), "coid": ck.get("u_co_id", ""),
            "uname": ck.get("u_name", ""), "coname": ck.get("u_co_name", "")}
    # 真实实现里 _apply_session 会把异同落到客户端对象上，打桩要保持一致
    server.JST.cookies = dict(ck)
    server.JST.uid, server.JST.coid = info["uid"], info["coid"]
    server.JST.uname, server.JST.coname = info["uname"], info["coname"]
    return info


server.JST.login = fake_login
server.JST.validcode_image = fake_validcode
server.JST.sms_send = fake_sms_send
server.JST.sms_login = fake_sms_login
server.JST.adopt_cookies = fake_adopt


# ------------------------------------------------------------ 伪造 Edge Cookie 库
class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def dpapi_protect(data):
    inp = _BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)),
                                       ctypes.POINTER(ctypes.c_char)))
    out = _BLOB()
    ok = ctypes.windll.crypt32.CryptProtectData(ctypes.byref(inp), None, None, None, None,
                                                0x01, ctypes.byref(out))
    if not ok:
        raise RuntimeError("CryptProtectData failed")
    try:
        return b"v10" + ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def make_fake_profile(name, rows):
    """造一个带 Network/Cookies 的目录，模拟 Edge 登录后落盘的 Cookie"""
    prof = os.path.join(TMP, name)
    d = os.path.join(prof, "Network")
    os.makedirs(d, exist_ok=True)
    db = os.path.join(d, "Cookies")
    if os.path.exists(db):
        os.remove(db)
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE cookies(host_key TEXT, name TEXT, value TEXT,
                   encrypted_value BLOB, expires_utc INTEGER, app_bound_encrypted_value BLOB)""")
    future = int((time.time() + 3600 + 11644473600) * 1e6)
    con.executemany("INSERT INTO cookies VALUES(?,?,?,?,?,?)",
                    [(h, n, "", dpapi_protect(v.encode("utf-8")), future, None)
                     for h, n, v in rows])
    con.commit()
    con.close()
    return prof


class _DeadProc(object):
    def poll(self):
        return 0


# ------------------------------------------------------------ 启动服务
class QuietHandler(server.Handler):
    def log_message(self, fmt, *a):
        pass


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


PORT = free_port()
BASE = "http://127.0.0.1:%d" % PORT
HTTPD = None


def call(path, body=None, raw=False):
    """发请求。4xx/5xx 也返回内容，方便断言错误分支的文案。"""
    path = urllib.parse.quote(path, safe="/?&=")
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(BASE + path, data=data, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            blob, ctype = r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        blob, ctype = e.read(), e.headers.get("Content-Type", "")
    if raw:
        return blob
    if ctype.startswith("application/json"):
        return json.loads(blob.decode())
    return {"ok": False, "msg": blob.decode("utf-8", "replace")}


def show(tag, r):
    msg = r.get("msg") or r.get("hint") or ""
    print("  %-30s ok=%s %s" % (tag, r.get("ok"), msg))


def check(cond, what, ctx=""):
    if not cond:
        print("  失败：" + what)
        if ctx:
            print("       ", ctx)
        raise AssertionError(what)
    print("  ✓ " + what)


def main():
    global HTTPD
    HTTPD = server.ThreadingHTTPServer(("127.0.0.1", PORT), QuietHandler)
    HTTPD.daemon_threads = True
    threading.Thread(target=HTTPD.serve_forever, daemon=True).start()
    time.sleep(0.3)
    try:
        run()
    finally:
        try:
            HTTPD.shutdown()
        except Exception:
            pass
        shutil.rmtree(TMP, ignore_errors=True)
    print("\n登录链路全部用例通过")
    return 0


def run():
    _real_take_cookies = server.qr_take_cookies          # 打桩前存原始实现
    print("== 环境 ==")
    print("  临时库：", os.path.join(TMP, "data", "jst.db"))

    print("== 图形验证码 ==")
    blob = call("/api/captcha?account=someone", raw=True)
    check(blob[:6] == b"GIF89a", "GET /api/captcha 返回图片二进制（%d 字节）" % len(blob))
    check(USES["captcha_img"] == 1, "验证码按账号向官方拉取一次")
    r = call("/api/captcha", raw=True)
    check(isinstance(r, bytes) and r.startswith(b"missing"), "缺账号时返回 400 而不是崩溃")

    print("== 账号密码登录 ==")
    r = call("/api/login", {"account": "needcaptcha", "password": "x"})
    show("触发图形验证码", r)
    check(r["ok"] and r["need_captcha"], "服务端要验证码时返回 need_captcha=True")
    check("验证码" in (r.get("msg") or ""), "提示文案透给用户")

    call("/api/login", {"account": "needcaptcha", "password": "x", "verify_code": "9999"})
    check(USES["login_args"][-1] == ("needcaptcha", "x", "9999"),
          "带上验证码后原样传给 Passport 接口", USES["login_args"])

    r = call("/api/login", {"account": "u", "password": "bad"})
    check((not r["ok"]) and "账号或密码错误" in r["msg"], "密码错误时给出明确原因")

    r = call("/api/login", {"account": "u"})
    check((not r["ok"]) and "请输入账号和密码" in r["msg"], "缺参数时拦截不调接口")

    print("== 短信验证码 ==")
    r = call("/api/sms/send", {"mobile": "13800000000"})
    show("绑定多账号→要求尾号", r)
    check(r["ok"] and r["need_suffix"] and r["prefix"] == "138****0000",
          "返回 need_suffix + 脱敏手机号前缀")
    r = call("/api/sms/login", {"mobile": "13800000000", "code": "123456"})
    txt = (r.get("msg") or "") + (r.get("hint") or "")
    check((not r["ok"]) and "重新点" in txt, "没走完尾号验证就登录 → 要求重新获取验证码")

    r = call("/api/sms/send", {"mobile": "13800000000", "suffix": "66"})
    show("补尾号后重发", r)
    check(r["ok"] and not r.get("need_suffix"), "第二次下发成功")
    check(USES["sms_send_args"][-1] == ("13800000000", "66"), "尾号透传给下发接口")

    r = call("/api/sms/login", {"mobile": "13800000000", "code": "123456", "suffix": "66"})
    show("短信登录", r)
    check(r["ok"] and r["user"]["uid"] == "9002", "登录成功并带回用户信息")
    check(USES["sms_login_args"][-1][2] == "CID-ABC-123", "cid 由服务端持有，不经过前端")

    r = call("/api/sms/send", {"mobile": "13900000000"})
    check((not r["ok"]) and "未绑定" in r["msg"], "未绑定手机号在下发阶段就被拦住")
    r = call("/api/sms/login", {"mobile": "13800000000", "code": "123456", "suffix": "66"})
    txt = (r.get("msg") or "") + (r.get("hint") or "")
    check((not r["ok"]) and "重新点" in txt, "凭据用掉以后不能复用")

    print("== 扫码登录（微信 / 钉钉） ==")
    r = call("/api/qr/finish", {})
    check((not r["ok"]) and "还没有打开" in r["msg"], "没开窗口就点取回 → 明确提示")

    # poll：窗口没开时静默返回 logged=False，前端继续等
    r = call("/api/qr/poll")
    check(r.get("logged") is False, "未开窗时 poll 静默等待")

    # 窗口「开着」但还没登录 → CDP 取不到 u_id → 继续等
    prof_empty = make_fake_profile("edge-empty", [(".erp321.com", "UM_distinctid", "abc")])
    server.QR_PROFILE = prof_empty
    server.QR_PROC = _DeadProc()
    server.QR_CDP_PORT = 0          # CDP 不可用 → take_cookies 返回 None
    r = call("/api/qr/poll")
    check(r.get("logged") is False, "扫了但还没登录成功 → poll 继续等待")

    # 手动 finish：CDP 不可用、窗口已退出 → 回退读 SQLite（空库）→ 明确报错
    r = call("/api/qr/finish", {})
    show("扫了但没登录成功", r)
    check((not r["ok"]) and ("u_id" in r["msg"] or "Cookie" in r["msg"]),
          "Cookie 里没有身份时给出可诊断的失败原因")

    # 正常扫码登录：模拟 CDP 通道取到会话 cookie
    server._qr_live_backup = server._qr_live
    server._qr_live = lambda: True
    server.qr_take_cookies = lambda: {
        "u_id": "88001", "u_co_id": "71234",
        "u_name": "扫码登录的用户", "u_co_name": "某某电商",
        "UM_distinctid": "noise",
    }
    closed = {"n": 0}
    server.qr_close = lambda wait=True: closed.__setitem__("n", closed["n"] + 1) or True

    r = call("/api/qr/poll")
    show("CDP 自动取回", r)
    check(r["logged"] and r["user"]["uid"] == "88001", "扫码确认后自动完成登录（无需点按钮）")
    check(closed["n"] == 1, "自动登录成功后顺手关掉扫码窗口")
    st = call("/api/state")
    check(st["logged"] and st["user"]["uid"] == "88001", "登录态已生效")

    server.qr_close = lambda wait=True: True
    server._qr_live = server._qr_live_backup
    server.qr_take_cookies = _real_take_cookies

    r = call("/api/qr/cancel", {})
    check(r["ok"], "取消扫码登录可以正常关闭窗口")

    r = call("/api/logout", {})
    check(r["ok"] and not call("/api/state")["logged"], "退出后清空会话")

    print("== 分仓 / 货主清单与查询上下文 ==")
    # 回归防护：_ctx_candidates 曾因读音未定义的 pref_o 抛 NameError，
    # 导致所有采购单查询在冒泡到 handler 兜底后统一报「无权限」——这里必须能跑通。
    try:
        cands = server._ctx_candidates()
        check(isinstance(cands, list), "查询上下文候选可正常生成（无 NameError）")
    except NameError as e:
        check(False, "查询上下文候选可正常生成（无 NameError）", str(e))
    check(server._ctx_candidates("12034457", "15195086")[0] == ("12034457", "15195086"),
          "用户显式指定的货主/分仓排在候选首位")

    # 接口返回形状兼容：{id: name} 字典 / 对象数组 / 二维数组都必须带出中文名
    from jst import JstClient as _JC

    def _wms(shape):
        c = _JC()
        c.json_api = lambda *a, **k: shape
        return {x["id"]: x["name"] for x in c.wms_list()}

    check(_wms({"15179484": "东莞至臻", "2147483647": "--所有仓储--"})
          == {"15179484": "东莞至臻", "2147483647": "--所有仓储--"},
          "分仓[字典形态]解析出中文名（不再显示数字）")
    check(_wms([{"co_id": 15195086, "co_name": "东莞品秀刷货仓"}])
          == {"15195086": "东莞品秀刷货仓"},
          "分仓[对象数组]解析出中文名")
    check(_wms([[15197438, "广州臻选"]]) == {"15197438": "广州臻选"},
          "分仓[二维数组]解析出中文名")

    def _pt(shape):
        c = _JC()
        c.json_api = lambda *a, **k: shape
        return {x["id"]: x["name"] for x in c.partners()}

    check(_pt([{"consignor_co_id": 12034457, "consignor_co_name": "品秀供应链"}])
          == {"12034457": "品秀供应链"}, "货主[数组]解析出中文名")
    check(_pt({"15485884": "至臻测试货主"}) == {"15485884": "至臻测试货主"},
          "货主[字典]解析出中文名")
    check(_wms({}) == {} and _pt([]) == {}, "清单为空时不抛异常")


if __name__ == "__main__":
    sys.exit(main())
