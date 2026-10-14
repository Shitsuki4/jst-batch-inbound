# -*- coding: utf-8 -*-
"""
登录自助诊断 —— 当界面登不进去时，用这个脚本看清服务端到底返回了什么。

用法（在本机「命令提示符」里，不要双击）：
    python app/diag.py                 # 交互输入账号密码
    python app/diag.py 13800138000     # 只带账号，密码再输一次

会依次检查：代理环境 → DNS → 直连通达性 → 真实登录请求（打印服务端原始响应）。
密码只在内存里使用，不写任何文件、不打日志。
"""
import getpass
import json
import os
import socket
import ssl
import sys
import time
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jst import LOGIN_URL, UA, JstClient, LoginError  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HOSTS = ["api.erp321.com", "www.erp321.com", "gias.jd.com", "g.alicdn.com"]


def line(t=""):
    print(t)


def check_env():
    line("【1】代理环境")
    proxies = urllib.request.getproxies()
    found = False
    for k in ("http_proxy", "https_proxy", "all_proxy", "no_proxy",
              "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
        v = os.environ.get(k)
        if v:
            line("    %s = %s" % (k, v))
            found = True
    if not found:
        line("    未设置任何代理环境变量")
    line("    urllib 实际使用：%s" % (proxies or "直连"))
    return proxies


def check_dns():
    line()
    line("【2】域名解析")
    ok = True
    for h in HOSTS:
        try:
            ip = socket.getaddrinfo(h, 443, socket.AF_INET)[0][4][0]
            line("    OK   %-20s -> %s" % (h, ip))
        except Exception as e:
            ok = False
            line("    失败 %-20s %s" % (h, e))
    return ok


def reach(opener, name):
    req = urllib.request.Request(
        "https://www.erp321.com/login.aspx", headers={"User-Agent": UA})
    t = time.time()
    try:
        with opener.open(req, timeout=15) as r:
            code = r.status
            return "OK    HTTP %s  %.2fs" % (code, time.time() - t)
    except urllib.error.HTTPError as e:
        return "OK    HTTP %s  %.2fs（服务端有响应，连通）" % (e.code, time.time() - t)
    except Exception as e:
        return "失败  %s: %s" % (type(e).__name__, str(e)[:160])


def check_reach():
    line()
    line("【3】到聚水潭的连通性")
    line("    走系统代理      %s" % reach(urllib.request.build_opener(), "proxy"))
    line("    直连（绕代理）  %s"
         % reach(urllib.request.build_opener(urllib.request.ProxyHandler({})), "direct"))


def do_login(account, password, jd3="", vd144="", opener=None):
    opener = opener or urllib.request.build_opener()
    body = json.dumps({"data": {"account": account, "password": password,
                                "j_d_3": jd3, "v_d_144": vd144,
                                "isApp": False}}, ensure_ascii=False).encode()
    req = urllib.request.Request(
        LOGIN_URL, data=body,
        headers={"User-Agent": UA, "Content-Type": "application/json",
                 "Accept": "application/json", "Origin": "https://www.erp321.com",
                 "Referer": "https://www.erp321.com/login.aspx"})
    with opener.open(req, timeout=25) as r:
        return r.status, r.read().decode("utf-8", "replace")


def check_login(account, password):
    line()
    line("【4】真实登录请求（不计入任何日志文件）")
    if not jd3_global:
        line("    未取到京东设备指纹 j_d_3，将以空值提交")
    try:
        status, raw = do_login(account, password, jd3_global, vd144_global)
    except urllib.error.HTTPError as e:
        line("    HTTP %s" % e.code)
        line("    响应体：%s" % e.read()[:400].decode("utf-8", "replace"))
        return
    except Exception as e:
        line("    请求异常：%s: %s" % (type(e).__name__, str(e)[:200]))
        line("    → 如果是隧道/代理报错，多半是本机代理拦了 HTTPS，见第 1 步")
        return

    line("    HTTP %s" % status)
    line("    原始响应前 500 字符：")
    for chunk in [raw[i:i + 100] for i in range(0, min(500, len(raw)), 100)]:
        line("        " + chunk)
    try:
        j = json.loads(raw)
    except Exception:
        line("    → 返回的不是 JSON（可能被网关/代理拦截）")
        return

    code = j.get("code")
    msg = j.get("msg")
    line()
    line("    code = %s" % code)
    line("    msg  = %s" % msg)
    d = j.get("data") or {}
    if isinstance(d, dict):
        for k in ("hasRisk", "idaasHasRisk", "safeLevel", "isTPWS", "mobile"):
            if k in d:
                line("    data.%s = %s" % (k, d[k]))
    line("    cookie = %s" % ("已下发 %d 项" % len(j["cookie"]) if j.get("cookie") else "无"))

    line()
    if code == 0 and j.get("cookie"):
        line("    ✅ 登录成功。若工具界面仍然登不上，请对比下面两项：")
        line("       · 界面是用同一个账号吗？")
        line("       · 关掉工具重开一次（会话 cookie 有时效）")
    elif code == 0:
        line("    ⚠ 返回成功但没有 cookie，通常是要走安全验证")
    else:
        line("    ❌ 服务端拒绝了登录，原因就是上面的 msg，请按它处理：")
        m = msg or ""
        if "禁用" in m:
            line("       → 账号被停用：让公司管理员在聚水潭「用户及权限」里启用该账号")
        elif "密码" in m:
            line("       → 账号或密码不对：注意大小写，或先在网页端改一次密码")
        elif "验证码" in m or "code" in m.lower():
            line("       → 需要先在网页端完成一次验证码登录")
        elif "验证码" not in m:
            line("       → 其它原因，以网页端登录看到的提示为准")


jd3_global = ""
vd144_global = ""


def main():
    global jd3_global, vd144_global
    line("=" * 62)
    line(" 聚水潭扫码入库 —— 登录诊断")
    line("=" * 62)
    check_env()
    check_dns()
    check_reach()

    account = sys.argv[1] if len(sys.argv) > 1 else input("\n请输入聚水潭账号：").strip()
    password = sys.argv[2] if len(sys.argv) > 2 else getpass.getpass("请输入密码（输入不回显）：")
    if not account or not password:
        line("账号和密码都不能为空")
        return 1
    check_login(account, password)
    line()
    line("诊断结束。把上面的输出截图给我，就能准确定位。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        line("\n已取消")
