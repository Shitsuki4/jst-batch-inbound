# -*- coding: utf-8 -*-
"""远程管理页经 cpolar 穿透访问的回归（4.1.9）。

cpolar 客户端装在收货电脑上，它把公网 https 地址收到的请求转给本机 127.0.0.1 的入口，
所以入口看到的来源地址永远是 127.0.0.1。这里用 127.0.0.1 上的明文 HTTP 模拟 cpolar 转发过来的请求
（Host / Origin 是 cpolar 的公网地址），重点看：
* 入口只绑 127.0.0.1，只说 HTTP，本机收货服务端口不能拿来穿透；
* 登录验证照旧，跨站请求照旧拒绝；
* 一个人输错密码，不能把 30 天内登录成功过的浏览器一起锁在外面；
* 有人登录成功后，整个 cpolar 入口不能变成"登录成功过的网络"而绕过全局暂停。
"""
import http.client
import json
import os
import socket
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("JST_REMOTE_TEST_BIND", "::1")

import remote_access  # noqa: E402
from remote_access import RemoteHost, Guard, SHARED_KEY  # noqa: E402
from remote_access_test import build_data, PASSWORD  # noqa: E402

CPOLAR_HOST = "3a5b7c9d.r6.cpolar.top"


def free_port4():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Browser:
    """一个浏览器：经 cpolar 访问，各自带自己的 Cookie。"""

    def __init__(self, port, host=CPOLAR_HOST, scheme="https"):
        self.port, self.host, self.scheme = port, host, scheme
        self.cookies = {}

    def req(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        h = {"Host": self.host}
        if self.cookies:
            h["Cookie"] = "; ".join("%s=%s" % kv for kv in self.cookies.items())
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
            h["Origin"] = "%s://%s" % (self.scheme, self.host)
            h["Sec-Fetch-Site"] = "same-origin"
        h.update(headers or {})
        conn.request(method, path, body=data, headers=h)
        r = conn.getresponse()
        raw = r.read()
        r.set_cookies = r.msg.get_all("Set-Cookie") or []
        conn.close()
        try:
            r.data = json.loads(raw.decode("utf-8"))
        except ValueError:
            r.data = None
        r.raw = raw
        return r

    def login(self, password=PASSWORD, user="admin"):
        r = self.req("POST", "/api/login", {"username": user, "password": password})
        for sc in r.set_cookies:
            name, _, rest = sc.partition("=")
            self.cookies[name] = rest.split(";", 1)[0]
        return r


class CpolarTunnelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="jst-cpolar-")
        self.d = os.path.join(self.temp.name, "data")
        os.makedirs(self.d)
        build_data(self.d)
        self.host = RemoteHost(self.d, service_port=8765)
        self.tport = free_port4()

    def tearDown(self):
        self.host.stop()
        self.host.close_log()
        self.temp.cleanup()

    def enable(self, **kw):
        body = {"enabled": True, "username": "admin", "password": PASSWORD, "ipv6_enabled": False,
                "tunnel_enabled": True, "tunnel_port": self.tport}
        body.update(kw)
        st = self.host.update(body)
        self.assertTrue(st["tunnel_running"], st)
        return st

    def read_log(self):
        with open(os.path.join(self.d, "logs", "remote-access.log"), encoding="utf-8") as f:
            return f.read()

    # ---------- 入口本身 ----------
    def test_old_config_keeps_ipv6_only(self):
        c = self.host.config()
        self.assertTrue(c["ipv6_enabled"])
        self.assertFalse(c["tunnel_enabled"])
        self.assertEqual(c["tunnel_port"], remote_access.DEFAULT_TUNNEL_PORT)

    def test_listener_is_loopback_plain_http_only(self):
        st = self.enable()
        self.assertFalse(st["ipv6_running"])
        self.assertEqual(st["cpolar_local_address"], "127.0.0.1:%d" % self.tport)
        self.assertEqual(self.host.tunnel.server_address[0], "127.0.0.1")
        r = Browser(self.tport).req("GET", "/")
        self.assertEqual(r.status, 200)
        self.assertIn("remote.js".encode(), r.raw)
        self.assertEqual(Browser(self.tport).req("GET", "/api/receipts").status, 401)
        self.assertEqual(Browser(self.tport).req("GET", "/api/state").status, 401)
        self.assertFalse(self.host.tunnel.verify_request(None, ("192.168.1.9", 5000)))
        self.assertTrue(self.host.tunnel.verify_request(None, ("127.0.0.1", 5000)))
        self.assertTrue(self.host.update({"enabled": False})["running"] is False)
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", self.tport), timeout=1).close()

    def test_config_validation(self):
        with self.assertRaises(ValueError):
            self.host.update({"enabled": True, "username": "admin", "password": PASSWORD,
                              "ipv6_enabled": False, "tunnel_enabled": False})
        with self.assertRaises(ValueError):   # 本机收货服务端口是完整收货界面，不能穿透
            self.host.update({"enabled": True, "username": "admin", "password": PASSWORD,
                              "tunnel_enabled": True, "tunnel_port": 8765})
        with self.assertRaises(ValueError):
            self.host.update({"enabled": True, "username": "admin", "password": PASSWORD, "port": 18500,
                              "tunnel_enabled": True, "tunnel_port": 18500})
        with self.assertRaises(ValueError):
            self.host.update({"enabled": True, "username": "admin", "password": PASSWORD,
                              "tunnel_enabled": "yes"})

    # ---------- 登录验证 ----------
    def test_login_required_and_works_through_cpolar_host(self):
        self.enable()
        b = Browser(self.tport)
        self.assertEqual(b.login("wrong-password").status, 401)
        r = b.login()
        self.assertEqual(r.status, 200, r.data)
        names = [c.split("=", 1)[0] for c in r.set_cookies]
        self.assertEqual(sorted(names), sorted([remote_access.COOKIE, remote_access.DEVICE_COOKIE]))
        for c in r.set_cookies:
            for flag in ("Secure", "HttpOnly", "SameSite=Strict", "Path=/"):
                self.assertIn(flag, c)
        r = b.req("GET", "/api/receipts?type=po")
        self.assertEqual(r.status, 200, r.data)
        self.assertTrue(r.data["rows"])
        self.assertIn("[cpolar] 登录成功", self.read_log())

    def test_cross_site_and_plain_http_origin_rejected(self):
        self.enable()
        evil = Browser(self.tport)
        r = evil.req("POST", "/api/login", {"username": "admin", "password": PASSWORD},
                     {"Origin": "https://evil.example"})
        self.assertEqual(r.status, 403)
        r = Browser(self.tport, scheme="http").login()   # cpolar 的 http 地址：页面脚本会先跳到 https
        self.assertEqual(r.status, 403)
        local = Browser(self.tport, host="127.0.0.1:%d" % self.tport, scheme="http")
        self.assertEqual(local.login().status, 200)     # 收货电脑本机自测

    # ---------- 锁定不误伤 ----------
    def test_strangers_failing_do_not_lock_out_signed_in_browser(self):
        self.enable()
        regular = Browser(self.tport)
        self.assertEqual(regular.login().status, 200)
        for i in range(remote_access.SHARED_FAIL_LIMIT):
            r = Browser(self.tport).login("wrong-%d" % i)
            self.assertEqual(r.status, 401)
        newcomer = Browser(self.tport)
        r = newcomer.login()
        self.assertEqual(r.status, 429, r.data)          # 新设备共用的桶被锁了 5 分钟
        self.assertIn("cpolar", r.data["msg"])
        self.assertEqual(regular.login().status, 200)    # 登录成功过的浏览器不受影响
        self.assertEqual(regular.req("GET", "/api/receipts?type=po").status, 200)

    def test_one_typo_does_not_lock_everyone(self):
        self.enable()
        for i in range(remote_access.FAIL_LIMIT):         # 4.1.8 及以前：5 次就锁 15 分钟，而且所有人共用 127.0.0.1
            self.assertEqual(Browser(self.tport).login("typo-%d" % i).status, 401)
        self.assertEqual(Browser(self.tport).login().status, 200)

    def test_success_does_not_make_whole_tunnel_trusted(self):
        self.enable()
        regular = Browser(self.tport)
        self.assertEqual(regular.login().status, 200)
        g = self.host.guard
        self.assertNotIn(SHARED_KEY, g.trusted)
        self.assertNotIn("127.0.0.1", g.trusted)
        g.global_until = time.time() + 300               # 模拟全局暂停（有人在刷密码）
        r = Browser(self.tport).login()
        self.assertEqual(r.status, 429, r.data)          # 陌生浏览器照样被暂停拦住
        self.assertEqual(regular.login().status, 200)    # 登录成功过的浏览器放行

    def test_password_change_forgets_devices(self):
        self.enable()
        regular = Browser(self.tport)
        self.assertEqual(regular.login().status, 200)
        self.assertEqual(len(self.host.guard.devices), 1)
        self.host.update({"password": "Another-Horse-77"})
        self.assertEqual(self.host.guard.devices, {})
        self.assertEqual(regular.req("GET", "/api/receipts?type=po").status, 401)

    def test_device_cookie_cannot_be_forged(self):
        self.enable()
        b = Browser(self.tport)
        b.cookies[remote_access.DEVICE_COOKIE] = "made-up-token"
        self.host.guard.global_until = time.time() + 300
        self.assertEqual(b.login().status, 429)


class SharedGuardTests(unittest.TestCase):
    def test_shared_bucket_limits(self):
        g = Guard()
        now = 1000.0
        for i in range(remote_access.SHARED_FAIL_LIMIT - 1):
            self.assertFalse(g.failed(SHARED_KEY, now + i))
        self.assertTrue(g.failed(SHARED_KEY, now + 20))
        self.assertIsNotNone(g.admit(SHARED_KEY, now + 21))
        later = now + 21 + remote_access.SHARED_LOCK_SECONDS + 1
        self.assertIsNone(g.admit(SHARED_KEY, later))

    def test_shared_success_keeps_others_failures(self):
        g = Guard()
        for i in range(3):
            g.failed(SHARED_KEY, 1000.0 + i)
        g.succeeded(SHARED_KEY, 1010.0)
        self.assertEqual(len(g.fails[SHARED_KEY]), 3)
        self.assertNotIn(SHARED_KEY, g.trusted)

    def test_device_token_lifecycle(self):
        g = Guard()
        t = g.trust_device(None, 1000.0)
        k = g.device_key(t, 1001.0)
        self.assertTrue(k.startswith("device:"))
        self.assertEqual(g.trust_device(t, 2000.0), t)    # 续期，不换令牌
        self.assertIsNone(g.device_key(t, 2000.0 + remote_access.TRUSTED_SECONDS + 1))
        self.assertIsNone(g.device_key("x" * 500, 1001.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
