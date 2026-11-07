# -*- coding: utf-8 -*-
"""内置内网穿透的回归（4.1.11）。

* cpolar：不随包发，第一次启用时从官方地址下载，核对 zip 和 cpolar.exe 两个 SHA-256，对不上就不运行；
  用一个假的 cpolar（Python 脚本）模拟日志和本机 /api/tunnels，看地址读取、地址变化、掉线重启、
  Authtoken 被拒不重启、停止时不留明文 token。
* Tailscale：假的 ra-tailnet 输出 JSON 状态，看状态映射、登录后删掉保存的 auth key、远程设置的校验；
  Tailscale 入口只接受本机连接、登录验证照旧、和 cpolar 的新设备各算各的失败次数。
* 真的 ra-tailnet（JST_TAILNET_EXE 或 backend 目录里有时）：不给 auth key 启动，应该报 NeedsLogin 并给出
  login.tailscale.com 的登录链接；stdin 关掉后自己退出。
"""
import hashlib
import http.client
import http.server
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("JST_REMOTE_TEST_BIND", "::1")

import tunnels  # noqa: E402
import remote_access  # noqa: E402
from remote_access import RemoteHost  # noqa: E402
from remote_access_test import build_data, PASSWORD  # noqa: E402
from remote_cpolar_test import free_port4  # noqa: E402

FAKE_CPOLAR = r'''
import http.server, json, os, sys, threading, time
args = sys.argv[1:]
conf = [a for a in args if a.startswith("-config=")][0][8:]
inspect = [a for a in args if a.startswith("-inspect-addr=")][0].split(":")[-1]
token = open(conf, encoding="utf-8").read().split(":", 1)[1].strip()
mark = os.path.join(os.path.dirname(conf), "runs.txt")
runs = len(open(mark).read().split()) if os.path.exists(mark) else 0
open(mark, "a").write("x ")
with open(os.path.join(os.path.dirname(conf), "argv.json"), "w") as f:
    json.dump(args, f)
if token == "BADTOKEN0123456789":
    print("Failed to authenticate to switch server: user authToken auth failed.", flush=True)
    sys.exit(1)
url = "https://run%d-abc.r6.cpolar.top" % runs
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        body = json.dumps({"tunnels": [{"public_url": url.replace("https", "http")}, {"public_url": url}]}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
srv = http.server.HTTPServer(("127.0.0.1", int(inspect)), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
print('time="x" level=info msg="Tunnel established"', flush=True)
life = float(os.environ.get("FAKE_LIFE", "60"))
end = time.time() + life
while time.time() < end:
    time.sleep(0.1)
sys.exit(3)
'''

FAKE_TAILNET = r'''
import json, os, sys, threading, time
args = sys.argv[1:]
sd = args[args.index("--state-dir") + 1]
os.makedirs(sd, exist_ok=True)
if "--logout" in args:
    open(os.path.join(sd, "logout.txt"), "w").write("1")
    print(json.dumps({"state": "LoggedOut"})); sys.exit(0)
key = os.environ.get("TS_AUTHKEY", "")
with open(os.path.join(sd, "seen.json"), "w") as f:
    json.dump({"args": args, "has_key": bool(key), "key_in_argv": any(key and key in a for a in args)}, f)
def out(d):
    print(json.dumps(d), flush=True)
out({"state": "Starting", "listen": 443})
if key:
    open(os.path.join(sd, "tailscaled.state"), "w").write("{}")
    out({"state": "Running", "ips": ["100.101.102.103", "fd7a:115c:a1e0::1"], "dns_name": "ra-test.tail1234.ts.net",
         "tailnet": "me@example.com", "listen": 443, "cert_mode": "self-signed", "cert_sha256": "AB" * 32})
else:
    out({"state": "NeedsLogin", "auth_url": "https://login.tailscale.com/a/abcdef", "listen": 443})
sys.stdin.read()
'''


def wait(pred, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


class ZipServer:
    """本机模拟 cpolar 官网的下载地址。"""

    def __init__(self, payload):
        self.payload = payload
        self.hits = 0
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.hits += 1
                self.send_response(200)
                self.send_header("Content-Length", str(len(outer.payload)))
                self.end_headers()
                self.wfile.write(outer.payload)

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d/cpolar-stable-windows-amd64.zip" % self.srv.server_address[1]

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def make_zip(name, content):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, content)
    return buf.getvalue()


class _CpolarBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cpolar-dl-")
        self.exe_bytes = FAKE_CPOLAR.encode("utf-8")
        self.zip = make_zip("fake_cpolar.py", self.exe_bytes)
        self.web = ZipServer(self.zip)
        self.logs = []

    def tearDown(self):
        self.web.close()

    def agent(self, **spec):
        base = dict(version="9.9.9", url=self.web.url, zip_sha256=hashlib.sha256(self.zip).hexdigest(),
                    exe_sha256=hashlib.sha256(self.exe_bytes).hexdigest(), exe="fake_cpolar.py")
        base.update(spec)
        return tunnels.CpolarAgent(self.logs.append, os.path.join(self.tmp, "tools"), os.path.join(self.tmp, "run"), base)


class CpolarDownloadTest(_CpolarBase):

    def test_pinned_constants_look_right(self):
        self.assertTrue(tunnels.CPOLAR_URL.startswith("https://www.cpolar.com/"))
        self.assertIn(tunnels.CPOLAR_VERSION, tunnels.CPOLAR_URL)
        for h in (tunnels.CPOLAR_ZIP_SHA256, tunnels.CPOLAR_EXE_SHA256):
            self.assertRegex(h, r"^[0-9a-f]{64}$")

    def test_download_verify_and_reuse(self):
        a = self.agent()
        exe = a.ensure_binary()
        self.assertEqual(open(exe, "rb").read(), self.exe_bytes)
        self.assertEqual(exe, os.path.join(self.tmp, "tools", "cpolar", "9.9.9", "fake_cpolar.py"))
        self.assertTrue(a.installed())
        a.ensure_binary()
        self.assertEqual(self.web.hits, 1, "已下载且校验通过时不应重复下载")
        # 本地文件被改动 → 删掉重新下载
        with open(exe, "ab") as f:
            f.write(b"# tampered")
        a.ensure_binary()
        self.assertEqual(self.web.hits, 2)
        self.assertEqual(open(exe, "rb").read(), self.exe_bytes)
        self.assertEqual([f for f in os.listdir(os.path.dirname(exe)) if f != "fake_cpolar.py"], [], "不应留下临时文件")

    def test_zip_hash_mismatch_refused(self):
        a = self.agent(zip_sha256="0" * 64)
        with self.assertRaises(tunnels._Fatal) as cm:
            a.ensure_binary()
        self.assertEqual(cm.exception.code, "E-RMT-1111")
        self.assertFalse(os.path.exists(a.exe_path()))
        self.assertEqual(os.listdir(os.path.dirname(a.exe_path())), [])

    def test_exe_hash_mismatch_refused(self):
        a = self.agent(exe_sha256="1" * 64)
        with self.assertRaises(tunnels._Fatal):
            a.ensure_binary()
        self.assertFalse(os.path.exists(a.exe_path()))

    def test_download_error_has_code(self):
        a = self.agent(url="http://127.0.0.1:9/nothing.zip")
        with self.assertRaises(Exception) as cm:
            a.ensure_binary()
        self.assertEqual(getattr(cm.exception, "code", ""), "E-RMT-1110")

    def test_pick_urls(self):
        self.assertEqual(tunnels.pick_urls({"tunnels": [{"public_url": "http://a.r6.cpolar.top"},
                                                        {"public_url": "https://a.r6.cpolar.top/"}]}),
                         ("https://a.r6.cpolar.top", "http://a.r6.cpolar.top"))
        self.assertEqual(tunnels.pick_urls({"tunnels": [{"public_url": "javascript:alert(1)"}]}), ("", ""))
        self.assertEqual(tunnels.pick_urls("junk"), ("", ""))


class CpolarRunTest(_CpolarBase):
    def test_run_reads_url_restarts_and_cleans_up(self):
        os.environ["FAKE_LIFE"] = "1.5"
        a = self.agent()
        a.launcher = [sys.executable]
        a.configure("GOODTOKEN0123456789", 18480)
        a.start()
        try:
            self.assertTrue(wait(lambda: a.public_url.startswith("https://run0-")), a.status())
            self.assertEqual(a.status()["state"], "online")
            self.assertEqual(a.status()["http_url"], "http://run0-abc.r6.cpolar.top")
            argv = json.load(open(os.path.join(self.tmp, "run", "argv.json")))
            self.assertIn("127.0.0.1:18480", argv)
            self.assertFalse(any("GOODTOKEN" in x for x in argv), "Authtoken 不能出现在命令行里")
            self.assertIn("-daemon=off", argv)
            # 假 cpolar 1.5 秒后退出：5 秒后自动重启，拿到新地址
            self.assertTrue(wait(lambda: a.public_url.startswith("https://run1-"), 15), a.status())
            self.assertTrue(any("已变为" in m for m in self.logs), self.logs)
        finally:
            os.environ.pop("FAKE_LIFE", None)
            a.stop()
        self.assertEqual(a.status()["state"], "off")
        self.assertEqual(a.public_url, "")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "run", "cpolar.yml")), "停止后不应留下明文 token")

    def test_bad_token_does_not_loop(self):
        a = self.agent()
        a.launcher = [sys.executable]
        a.configure("BADTOKEN0123456789", 18480)
        a.start()
        try:
            self.assertTrue(wait(lambda: a.status()["error_code"] == "E-RMT-1112"), a.status())
            time.sleep(6.5)
            runs = open(os.path.join(self.tmp, "run", "runs.txt")).read().split()
            self.assertEqual(len(runs), 1, "Authtoken 被拒后不应反复重启")
            self.assertFalse(a.want)
        finally:
            a.stop()

    def test_missing_token(self):
        a = self.agent()
        a.configure("", 18480)
        a.start()
        self.assertTrue(wait(lambda: a.status()["error_code"] == "E-RMT-1112"))
        self.assertEqual(self.web.hits, 0)


class TailnetAgentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tailnet-")
        self.exe = os.path.join(self.tmp, "fake_tailnet.py")
        with open(self.exe, "w", encoding="utf-8") as f:
            f.write(FAKE_TAILNET)

    def agent(self):
        a = tunnels.TailnetAgent(lambda m: None, self.exe, os.path.join(self.tmp, "state"))
        a.launcher = [sys.executable]
        return a

    def test_needs_login_then_authkey(self):
        a = self.agent()
        a.configure("", "ra-test", 443, 18481)
        a.start()
        try:
            self.assertTrue(wait(lambda: a.status()["state"] == "needs_login"), a.status())
            self.assertEqual(a.status()["auth_url"], "https://login.tailscale.com/a/abcdef")
            seen = json.load(open(os.path.join(self.tmp, "state", "seen.json")))
            self.assertIn("127.0.0.1:18481", seen["args"])
        finally:
            a.stop()
        got = []
        a = self.agent()
        a.on_logged_in = lambda: got.append(1)
        a.configure("tskey-auth-kAbc123-SECRETSECRET", "ra-test", 443, 18481)
        a.start()
        try:
            self.assertTrue(wait(lambda: a.status()["state"] == "online"), a.status())
            st = a.status()
            self.assertEqual(st["urls"][0], "https://ra-test.tail1234.ts.net/")
            self.assertIn("https://100.101.102.103/", st["urls"])
            self.assertEqual(st["auth_url"], "")
            self.assertTrue(got)
            seen = json.load(open(os.path.join(self.tmp, "state", "seen.json")))
            self.assertTrue(seen["has_key"])
            self.assertFalse(seen["key_in_argv"], "auth key 只能走环境变量")
        finally:
            a.stop()
        self.assertEqual(a.status()["state"], "off")

    def test_logout_removes_state(self):
        a = self.agent()
        os.makedirs(a.state_dir)
        open(os.path.join(a.state_dir, "tailscaled.state"), "w").write("{}")
        self.assertEqual(a.logout(), "")
        self.assertFalse(os.path.exists(a.state_dir))

    def test_missing_binary(self):
        a = tunnels.TailnetAgent(lambda m: None, os.path.join(self.tmp, "nope.exe"), os.path.join(self.tmp, "s"))
        a.start()
        self.assertTrue(wait(lambda: a.status()["error_code"] == "E-RMT-1122"))


class RemoteSettingsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="jst-tun-")
        self.data = os.path.join(self.temp.name, "data")
        os.makedirs(self.data)
        build_data(self.data)
        self.host = RemoteHost(self.data, service_port=8765)
        fake = os.path.join(self.data, "fake_tailnet.py")
        with open(fake, "w", encoding="utf-8") as f:
            f.write(FAKE_TAILNET)
        self.host.ts.exe = fake
        self.host.ts.launcher = [sys.executable]
        # 不让测试真的去 cpolar 官网下载：Windows 上下载线程还开着临时 zip 时，tearDown 删目录会失败
        self.host.cpolar.spec["url"] = "http://127.0.0.1:9/cpolar-test.zip"

    def tearDown(self):
        self.host.stop()
        self.host.close_log()
        self.temp.cleanup()

    def base(self, **kw):
        b = {"enabled": True, "ipv6_enabled": False, "tunnel_enabled": False, "tailscale_enabled": True,
             "tailscale_port": free_port4(), "port": 18443, "tunnel_port": free_port4(), "username": "admin",
             "password": PASSWORD}
        b.update(kw)
        return b

    def test_validation(self):
        for bad, frag in [({"tailscale_authkey": "abc"}, "tskey-"), ({"cpolar_token": "中文"}, "Authtoken"),
                          ({"tailscale_hostname": "Bad Name"}, "设备名"), ({"tailscale_port": 8765}, "端口"),
                          ({"tailscale_enabled": False}, "至少选一种")]:
            with self.assertRaises(ValueError) as cm:
                self.host.update(self.base(**bad))
            self.assertIn(frag, str(cm.exception))

    def test_secrets_saved_protected_and_authkey_forgotten(self):
        st = self.host.update(self.base(tailscale_authkey="tskey-auth-kAbc123-SECRETSECRET", tunnel_enabled=True,
                                        cpolar_token="cpolar authtoken ZmFrZXRva2VuZmFrZXRva2Vu"))
        raw = open(self.host.config_path, encoding="utf-8").read()
        self.assertNotIn("ZmFrZXRva2VuZmFrZXRva2Vu", raw.replace("plain:", "") if os.name == "nt" else "")
        c = self.host.config()
        self.assertEqual(tunnels.unprotect(c["cpolar_token"]), "ZmFrZXRva2VuZmFrZXRva2Vu", "应去掉粘贴时带的命令前缀")
        self.assertTrue(st["cpolar"]["builtin"])
        self.assertNotIn("ZmFrZXRva2Vu", json.dumps(st), "状态里不能带出 token")
        self.assertNotIn("SECRETSECRET", json.dumps(st))
        self.assertTrue(wait(lambda: self.host.status()["tailscale"]["state"] == "online"), self.host.status()["tailscale"])
        self.assertTrue(wait(lambda: self.host.config()["tailscale_authkey"] == ""), "登录后应删掉保存的 auth key")
        s = self.host.status()
        self.assertTrue(s["tailscale_running"])
        self.assertIn("https://ra-test.tail1234.ts.net/", s["tailscale"]["urls"])
        # 清空 cpolar token
        self.host.update(self.base(cpolar_token_clear=True, tunnel_enabled=True, password=""))
        self.assertEqual(self.host.config()["cpolar_token"], "")
        # 退出登录：状态目录删掉，重新出现登录链接
        self.host.tailscale_logout()
        self.assertTrue(wait(lambda: self.host.status()["tailscale"]["state"] == "needs_login"), self.host.status()["tailscale"])

    def test_tailscale_entry_login_and_separate_bucket(self):
        st = self.host.update(self.base())
        port = st["tailscale_port"]
        self.assertTrue(st["tailscale_running"])
        host = "ra-test.tail1234.ts.net"

        def login(pw):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            body = json.dumps({"username": "admin", "password": pw})
            c.request("POST", "/api/login", body, {"Host": host, "Origin": "https://" + host,
                                                  "Content-Type": "application/json"})
            r = c.getresponse()
            out = (r.status, r.read().decode("utf-8"), r.getheader("Set-Cookie") or "")
            c.close()
            return out

        code, body, cookie = login(PASSWORD)
        self.assertEqual(code, 200, body)
        self.assertIn("Secure", cookie)
        for _ in range(remote_access.SHARED_FAIL_LIMIT):
            code, body, _ = login("wrong-password-xx")
        self.assertIn("Tailscale", body)
        self.assertIsNone(self.host.guard.admit(remote_access.SHARED_KEY), "Tailscale 入口的失败不该锁住 cpolar 的新设备")
        # 跨站请求照旧拒绝
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("POST", "/api/login", "{}", {"Host": host, "Origin": "https://evil.example", "Content-Type": "application/json"})
        self.assertEqual(c.getresponse().status, 403)
        c.close()


class RealTailnetBinaryTest(unittest.TestCase):
    """CI 里编译出的真 ra-tailnet：不给 auth key 应报 NeedsLogin 和登录链接；关掉 stdin 就退出。"""

    def test_needs_login_and_exit_on_stdin_close(self):
        exe = os.environ.get("JST_TAILNET_EXE") or tunnels.default_tailnet_exe()
        if not os.path.isfile(exe):
            self.skipTest("没有 ra-tailnet（设 JST_TAILNET_EXE 指向编译结果）")
        sd = tempfile.mkdtemp(prefix="ra-tailnet-")
        env = dict(os.environ)
        env.pop("TS_AUTHKEY", None)
        p = subprocess.Popen([exe, "--state-dir", sd, "--listen", str(free_port4()), "--target", "127.0.0.1:%d" % free_port4()],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
        states = []
        try:
            end = time.time() + 60
            while time.time() < end:
                line = p.stdout.readline()
                if not line:
                    break
                d = json.loads(line)
                states.append(d)
                if d.get("state") == "NeedsLogin" and d.get("auth_url"):
                    break
            self.assertTrue(states and states[-1].get("state") == "NeedsLogin", states)
            self.assertTrue(states[-1]["auth_url"].startswith("https://login.tailscale.com/"))
            self.assertRegex(states[-1].get("cert_sha256", ""), r"^[0-9A-F]{64}$")
            p.stdin.close()
            p.wait(timeout=15)
        finally:
            if p.poll() is None:
                p.kill()
        # 拒绝转发到非 127.0.0.1
        r = subprocess.run([exe, "--state-dir", sd, "--target", "0.0.0.0:80"], stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("127.0.0.1", r.stdout.decode("utf-8", "replace"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
