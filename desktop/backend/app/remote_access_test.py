# -*- coding: utf-8 -*-
"""远程管理页（IPv6 只读）回归：全部使用临时目录与 IPv6 回环 ::1，不接触真实数据和公网。"""
import hashlib
import http.client
import json
import os
import socket
import sqlite3
import ssl
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["JST_REMOTE_TEST_BIND"] = "::1"

import remote_access  # noqa: E402
from remote_access import RemoteHost, Guard, ip_key, verify_password, hash_password, _range  # noqa: E402
from store import Store  # noqa: E402

PASSWORD = "Correct-Horse-9"
VIDEO_BYTES = bytes((i * 7 + 3) % 256 for i in range(300 * 1024))


def ipv6_ok():
    if not socket.has_ipv6:
        return False
    s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    try:
        s.bind(("::1", 0))
        return True
    except OSError:
        return False
    finally:
        s.close()


def free_port():
    s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    s.bind(("::1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def port_open(port):
    s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        s.connect(("::1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def build_data(d):
    """三类收货 + 录像目录（含旁注、关联索引、越界旁注）。"""
    st = Store(os.path.join(d, "jst.db"))
    items = [{"poi_id": "1", "sku_id": "A1", "sku_code": "690001", "name": "测试散粉", "qty": 10},
             {"poi_id": "2", "sku_id": "B2", "sku_code": "690002", "name": "测试口红", "qty": 5}]
    sid = st.open_session("PO-77", "PO-77", "po_id", {"supplier": "远程供应商", "wh_name": "主仓", "outer_po_id": "OUT-9"}, items)
    b, _ = st.ensure_batch(sid, "SF123456789")
    st.add_qty(b["id"], sid, {"sku_id": "A1", "name": "测试散粉", "sku_code": "690001"}, 10)
    st.add_qty(b["id"], sid, {"sku_id": "B2", "name": "测试口红", "sku_code": "690002"}, 3)
    st.add_qty(b["id"], sid, {"sku_id": "Z9", "name": "多出来的赠品", "matched": False}, 1)
    st.db.execute("INSERT INTO inbound_job(session_id,fingerprint,snapshot_json,login_co_id,status,io_id,error,created_at,updated_at)"
                  " VALUES(?,?,?,?,?,?,?,?,?)", (sid, "fp1", "{\"secret\":1}", "1", "complete", "IO-1", "ERP-SECRET", "2026-10-20 10:00:00", "2026-10-20 10:01:00"))
    st.db.commit()
    st.db.close()
    un = Store(os.path.join(d, "unplanned.db"))
    un.db.execute('CREATE TABLE IF NOT EXISTS unplanned_meta(sid INTEGER PRIMARY KEY, note TEXT NOT NULL DEFAULT "")')
    c = un.db.execute("INSERT INTO session(input_no,po_id,id_type,state,started_at,items_json) VALUES('WP20261020-0001','','unplanned','active',?, '[]')",
                      (time.strftime("%Y-%m-%d %H:%M:%S"),))
    usid = c.lastrowid
    un.db.execute("INSERT INTO unplanned_meta(sid,note) VALUES(?,?)", (usid, "退货包裹"))
    un.db.commit()
    ub, _ = un.ensure_batch(usid, "YT0001")
    un.add_qty(ub["id"], usid, {"sku_id": "Q1", "name": "无单商品"}, 2)
    un.db.close()
    jo = Store(os.path.join(d, "joint_receipts.db"))
    jo.db.executescript("""
    CREATE TABLE IF NOT EXISTS joint_group(sid INTEGER PRIMARY KEY,account TEXT NOT NULL,mother TEXT NOT NULL,expected INTEGER,created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS joint_purchase(id INTEGER PRIMARY KEY,group_sid INTEGER NOT NULL,po_id TEXT NOT NULL,owner TEXT NOT NULL,auth TEXT NOT NULL,head_json TEXT NOT NULL,initial_json TEXT NOT NULL,live_json TEXT NOT NULL,read_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS joint_stage(sid INTEGER PRIMARY KEY,group_sid INTEGER NOT NULL,purchase_id INTEGER NOT NULL,mode TEXT NOT NULL,purchase_digest TEXT NOT NULL,cancelled INTEGER NOT NULL DEFAULT 0,created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS joint_allocation(id INTEGER PRIMARY KEY,group_sid INTEGER NOT NULL,stage_sid INTEGER NOT NULL,source_line INTEGER NOT NULL,qty TEXT NOT NULL);
    """)
    c = jo.db.execute("INSERT INTO session(input_no,po_id,id_type,state,started_at,items_json) VALUES('LH-1','','unplanned','closed',?,'[]')",
                      (time.strftime("%Y-%m-%d %H:%M:%S"),))
    jsid = c.lastrowid
    jo.db.execute("INSERT INTO joint_group VALUES(?,?,?,?,?)", (jsid, "[]", "MOTHER-1", 2, "2026-10-20"))
    jo.db.execute("INSERT INTO joint_purchase(group_sid,po_id,owner,auth,head_json,initial_json,live_json,read_at) VALUES(?,?,?,?,?,?,?,?)",
                  (jsid, "PO-J1", "1", "1", json.dumps({"outer_po_id": "OJ1", "supplier_name": "联合供应商"}), "[]", "[]", "x"))
    jo.db.commit()
    jb, _ = jo.ensure_batch(jsid, "JDJOINT01")
    jo.add_qty(jb["id"], jsid, {"sku_id": "J1", "name": "联合商品"}, 4)
    jo.db.close()

    # 快递单登记：单独的 express.db；KD-NOVIDEO-77 没有录像
    ex = Store(os.path.join(d, "express.db"))
    ex.db.execute('CREATE TABLE IF NOT EXISTS unplanned_meta(sid INTEGER PRIMARY KEY, note TEXT NOT NULL DEFAULT "")')
    c = ex.db.execute("INSERT INTO session(input_no,po_id,id_type,state,started_at,items_json) VALUES('KD20261020-0001','','express','closed',?, '[]')",
                      (time.strftime("%Y-%m-%d %H:%M:%S"),))
    esid = c.lastrowid
    ex.db.execute("INSERT INTO unplanned_meta(sid,note) VALUES(?,?)", (esid, "到付快递"))
    ex.db.commit()
    eb, _ = ex.ensure_batch(esid, "KD-NOVIDEO-77")
    ex.add_qty(eb["id"], esid, {"sku_id": "E1", "name": "快递件"}, 1)
    ex.db.close()

    root = os.path.join(os.path.dirname(d), "录像")   # 与正式版一致：v3\\data 旁边的 v3\\录像
    folder = os.path.join(root, "2026年10月20日", "PO-77-OUT-9", "SF123456789")
    os.makedirs(folder)
    media = os.path.join(folder, "SF123456789.mp4")
    with open(media, "wb") as f:
        f.write(VIDEO_BYTES)
    now = time.strftime("%Y-%m-%dT%H:%M:%S.1234567+08:00")
    side = {"ArchiveVersion": 2, "Id": "a" * 32, "Tracking": "SF123456789", "BatchKey": "普通:1:1",
            "Context": {"Source": "普通", "SessionId": 1, "BatchId": 1, "Tracking": "SF123456789",
                        "Purchases": [{"Po": "PO-OLD", "Outer": ""}], "LastSku": "A1"},
            "RequestedAt": now, "FirstFrameAt": now, "EndAt": now, "File": media, "State": "saved",
            "Duration": 12.5, "Note": "外箱破损", "Protected": True, "Photos": [],
            "Cues": [{"Offset": 3.2, "Text": "A1 测试散粉", "Quantity": 2}, {"Offset": 8, "Text": "B2"}]}
    with open(media + ".json", "w", encoding="utf-8") as f:
        json.dump(side, f, ensure_ascii=False)
    with open(os.path.join(root, "video-associations.json"), "w", encoding="utf-8") as f:
        json.dump({"普通:1": {"Source": "普通", "SessionId": 1, "BatchId": 1, "Tracking": "SF123456789",
                              "Purchases": [{"Po": "PO-77", "Outer": "OUT-9"}], "LastSku": "A1"}}, f, ensure_ascii=False)
    # 越界旁注：File 指向根目录外的文件，绝不能被索引或播放。
    outside = os.path.join(d, "secret.mp4")
    with open(outside, "wb") as f:
        f.write(b"SECRET")
    evil_dir = os.path.join(root, "2026年10月20日", "evil")
    os.makedirs(evil_dir)
    evil = dict(side, Id="b" * 32, Tracking="EVIL", File=outside)
    with open(os.path.join(evil_dir, "x.mp4.json"), "w", encoding="utf-8") as f:
        json.dump(evil, f)
    # 链接目录（POSIX 可建）：不跟随。
    try:
        link_target = os.path.join(d, "linked")
        os.makedirs(link_target)
        lm = os.path.join(link_target, "L.mp4")
        with open(lm, "wb") as f:
            f.write(b"LINKED")
        os.symlink(link_target, os.path.join(root, "link"), target_is_directory=True)
        ls = dict(side, Id="c" * 32, Tracking="LINKED", File=os.path.join(root, "link", "L.mp4"))
        with open(os.path.join(link_target, "L.mp4.json"), "w", encoding="utf-8") as f:
            json.dump(ls, f)
    except (OSError, NotImplementedError):
        pass
    return {"sid": sid, "usid": usid, "jsid": jsid, "esid": esid, "root": root, "media": media}


class Client:
    def __init__(self, port):
        self.port = port
        self.ctx = ssl._create_unverified_context()   # 测试专用：自签名证书，指纹另行核对
        self.cookie = ""

    def req(self, method, path, body=None, headers=None):
        conn = http.client.HTTPSConnection("::1", self.port, context=self.ctx, timeout=15)
        h = {}
        if self.cookie:
            h["Cookie"] = self.cookie
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        conn.request(method, path, body=data, headers=h)
        r = conn.getresponse()
        raw = r.read()
        cert = conn.sock.getpeercert(binary_form=True) if conn.sock else None
        conn.close()
        r.raw, r.cert = raw, cert
        try:
            r.data = json.loads(raw.decode("utf-8"))
        except ValueError:
            r.data = None
        return r

    def login(self, user="admin", password=PASSWORD):
        r = self.req("POST", "/api/login", {"username": user, "password": password})
        sc = r.getheader("Set-Cookie") or ""
        if r.status == 200:
            self.cookie = sc.split(";", 1)[0]
        return r


@unittest.skipUnless(ipv6_ok(), "IPv6 loopback ::1 unavailable")
class RemoteAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="jst-remote-")
        self.d = os.path.join(self.temp.name, "data")
        os.makedirs(self.d)
        self.info = build_data(self.d)
        self.host = RemoteHost(self.d)
        self.port = free_port()

    def tearDown(self):
        self.host.stop()
        self.host.close_log()
        self.temp.cleanup()

    @staticmethod
    def read(path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def read_log(self):
        return self.read(os.path.join(self.d, "logs", "remote-access.log"))

    def enable(self, **kw):
        body = {"enabled": True, "port": self.port, "username": "admin", "password": PASSWORD}
        body.update(kw)
        st = self.host.update(body)
        self.assertTrue(st["running"], st)
        return Client(self.port)

    # ---------- 默认关闭 / 绑定 ----------
    def test_disabled_by_default_and_binding_only_when_enabled(self):
        self.assertFalse(self.host.config()["enabled"])
        self.host.start_if_enabled()
        self.assertIsNone(self.host.server)
        st = self.host.status()
        self.assertFalse(st["running"])
        self.assertFalse(port_open(self.port))
        with self.assertRaises(ValueError):
            self.host.update({"enabled": True, "port": self.port, "username": "admin"})   # 无密码不能启用
        self.assertFalse(port_open(self.port))
        self.enable()
        self.assertTrue(port_open(self.port))
        new_port = free_port()
        self.host.update({"port": new_port})
        self.assertTrue(port_open(new_port))
        self.assertFalse(port_open(self.port))
        self.host.update({"enabled": False})
        self.assertIsNone(self.host.server)
        self.assertFalse(port_open(new_port))

    def test_password_stored_as_salted_hash_only(self):
        with self.assertRaises(ValueError):
            self.host.update({"enabled": True, "port": self.port, "username": "admin", "password": "short"})
        with self.assertRaises(ValueError):
            self.host.update({"enabled": True, "port": 80, "username": "admin", "password": PASSWORD})
        self.enable()
        raw = self.read(self.host.config_path)
        self.assertNotIn(PASSWORD, raw)
        c = json.loads(raw)["password"]
        self.assertEqual(c["algo"], "scrypt")
        self.assertTrue(c["salt"])
        self.assertTrue(verify_password(PASSWORD, c))
        self.assertFalse(verify_password(PASSWORD + "x", c))
        self.assertNotEqual(hash_password(PASSWORD)["hash"], hash_password(PASSWORD)["hash"])

    def test_https_certificate_fingerprint_matches_status(self):
        cl = self.enable()
        r = cl.req("GET", "/")
        self.assertEqual(r.status, 200)
        self.assertIn("收货小助手", r.raw.decode("utf-8"))
        fp = hashlib.sha256(r.cert).hexdigest().upper()
        self.assertEqual(self.host.status()["fingerprint"].replace(":", ""), fp)
        self.assertIn("default-src 'none'", r.getheader("Content-Security-Policy"))
        # 明文 HTTP 不会得到页面
        s = socket.create_connection(("::1", self.port), timeout=5)
        s.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        try:
            data = s.recv(100)
        except OSError:
            data = b""
        s.close()
        self.assertNotIn(b"200 OK", data)

    # ---------- 认证 ----------
    def test_auth_required_on_every_route(self):
        cl = self.enable()
        vid = "a" * 32
        for path in ["/api/me", "/api/receipts", "/api/receipts/detail?type=po&id=1", "/api/videos",
                     "/api/videos/detail?id=" + vid, "/media/%s.mp4" % vid, "/media/%s.mp4?download=1" % vid,
                     "/api/state", "/api/settings", "/api/pda/status", "/files/x.csv", "/api/heartbeat", "/nope"]:
            r = cl.req("GET", path)
            self.assertEqual(r.status, 401, path)
            self.assertNotIn(b"PO-77", r.raw)
        for path in ["/api/logout", "/api/settings", "/api/inbound/create", "/api/report/clipboard", "/api/session/del"]:
            self.assertEqual(cl.req("POST", path, {}).status, 401, path)

    def test_existing_api_routes_not_reachable_after_login(self):
        cl = self.enable()
        self.assertEqual(cl.login().status, 200)
        self.assertEqual(cl.req("GET", "/api/me").status, 200)
        for path in ["/api/state", "/api/settings", "/api/heartbeat", "/api/pda/status", "/api/report/clipboard/status",
                     "/api/remote/status", "/pda.html", "/app.js", "/files/x.csv", "/api/session/detail?sid=1",
                     "/api/native/health", "/api/export"]:
            r = cl.req("GET", path)
            self.assertEqual(r.status, 404, path)
        for path in ["/api/settings", "/api/inbound/create", "/api/report/clipboard", "/api/session/del",
                     "/api/remote/config", "/api/pda/start", "/api/native/shutdown", "/api/item/scan"]:
            r = cl.req("POST", path, {})
            self.assertEqual(r.status, 404, path)
        for method in ("PUT", "DELETE", "PATCH", "OPTIONS"):
            self.assertEqual(cl.req(method, "/api/receipts").status, 405)

    def test_cookie_flags_and_logout_csrf(self):
        cl = self.enable()
        r = cl.login()
        sc = r.getheader("Set-Cookie")
        for flag in ("__Host-", "Secure", "HttpOnly", "SameSite=Strict", "Path=/"):
            self.assertIn(flag, sc)
        self.assertNotIn("Domain", sc)
        me = cl.req("GET", "/api/me").data
        self.assertEqual(cl.req("POST", "/api/logout", {}).status, 403)
        self.assertEqual(cl.req("POST", "/api/logout", {}, {"X-CSRF-Token": "wrong"}).status, 403)
        self.assertEqual(cl.req("POST", "/api/logout", {}, {"X-CSRF-Token": me["csrf"]}).status, 200)
        self.assertEqual(cl.req("GET", "/api/me").status, 401)

    def test_cross_origin_login_rejected(self):
        cl = self.enable()
        r = cl.req("POST", "/api/login", {"username": "admin", "password": PASSWORD}, {"Origin": "https://evil.example"})
        self.assertEqual(r.status, 403)
        r = cl.req("POST", "/api/login", {"username": "admin", "password": PASSWORD}, {"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(r.status, 403)
        # 表单编码（可被跨站表单伪造）不被接受
        conn = http.client.HTTPSConnection("::1", self.port, context=cl.ctx, timeout=10)
        conn.request("POST", "/api/login", body="username=admin&password=" + PASSWORD,
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(conn.getresponse().status, 400)
        conn.close()

    def test_lockout_after_repeated_failures(self):
        cl = self.enable()
        statuses = [cl.login(password="wrong-password-%d" % i).status for i in range(5)]
        self.assertEqual(statuses, [401] * 5)
        r = cl.login()
        self.assertEqual(r.status, 429)   # 正确密码也被锁定
        self.assertEqual(cl.cookie, "")
        log = self.read_log()
        self.assertIn("登录失败", log)
        self.assertIn("已触发锁定", log)
        self.assertNotIn(PASSWORD, log)
        self.assertNotIn("wrong-password", log)

    def test_session_idle_and_absolute_expiry_and_credential_change(self):
        cl = self.enable()
        cl.login()
        self.assertEqual(cl.req("GET", "/api/me").status, 200)
        with self.host.guard.lock:
            for s in self.host.guard.sessions.values():
                s["last"] = time.time() - remote_access.IDLE_TIMEOUT - 1
        self.assertEqual(cl.req("GET", "/api/me").status, 401)
        cl.login()
        with self.host.guard.lock:
            for s in self.host.guard.sessions.values():
                s["created"] = time.time() - remote_access.ABSOLUTE_TIMEOUT - 1
        self.assertEqual(cl.req("GET", "/api/me").status, 401)
        cl.login()
        self.assertEqual(cl.req("GET", "/api/me").status, 200)
        self.host.update({"password": "Another-Strong-7"})
        cl2 = Client(self.host.port())
        cl2.cookie = cl.cookie
        self.assertEqual(cl2.req("GET", "/api/me").status, 401)
        self.assertEqual(cl2.login(password="Another-Strong-7").status, 200)

    # ---------- 收货历史 ----------
    def test_receipt_history_and_detail(self):
        cl = self.enable()
        cl.login()
        rows = cl.req("GET", "/api/receipts?type=all").data["rows"]
        self.assertEqual(sorted(r["type"] for r in rows), ["express", "joint", "po", "unplanned"])
        po = cl.req("GET", "/api/receipts?type=po&q=SF1234").data["rows"]
        self.assertEqual(len(po), 1)
        self.assertEqual(po[0]["po_id"], "PO-77")
        self.assertEqual(po[0]["inbound"], [{"status": "complete", "io_id": "IO-1"}])
        self.assertEqual(len(cl.req("GET", "/api/receipts?type=po&q=" + "%E5%8F%A3%E7%BA%A2").data["rows"]), 1)   # 口红
        self.assertEqual(len(cl.req("GET", "/api/receipts?type=po&q=nothing").data["rows"]), 0)
        self.assertEqual(len(cl.req("GET", "/api/receipts?type=all&from=2000-01-01&to=2000-01-02").data["rows"]), 0)
        self.assertEqual(cl.req("GET", "/api/receipts?type=all&from=bad").status, 400)
        self.assertEqual(len(cl.req("GET", "/api/receipts?type=joint&q=PO-J1").data["rows"]), 1)
        self.assertEqual(len(cl.req("GET", "/api/receipts?type=unplanned&q=%E9%80%80%E8%B4%A7").data["rows"]), 1)
        d = cl.req("GET", "/api/receipts/detail?type=po&id=%d" % self.info["sid"]).data
        status = {r["sku_id"]: r["status"] for r in d["compare"]}
        self.assertEqual(status["A1"], "一致")
        self.assertEqual(status["B2"], "少收 2")
        self.assertEqual(status["Z9"], "采购单外")
        self.assertEqual(d["batches"][0]["tracking_no"], "SF123456789")
        self.assertNotIn("ERP-SECRET", json.dumps(d, ensure_ascii=False))
        self.assertNotIn("secret", json.dumps(d["inbound"]))
        j = cl.req("GET", "/api/receipts/detail?type=joint&id=%d" % self.info["jsid"]).data
        self.assertEqual(j["purchases"][0]["po_id"], "PO-J1")
        # 快递单登记：按物流单号（不分大小写、部分号码）、单据号、备注都能搜到，「全部」里也有
        for q in ("KD-NOVIDEO-77", "kd-novideo", "KD20261020", "%E5%88%B0%E4%BB%98"):   # 到付
            ex = cl.req("GET", "/api/receipts?type=express&q=" + q).data["rows"]
            self.assertEqual([r["id"] for r in ex], [self.info["esid"]], q)
        allx = cl.req("GET", "/api/receipts?type=all&q=NOVIDEO").data["rows"]
        self.assertEqual([(r["type"], r["id"]) for r in allx], [("express", self.info["esid"])])
        e = cl.req("GET", "/api/receipts/detail?type=express&id=%d" % self.info["esid"]).data
        self.assertEqual(e["batches"][0]["tracking_no"], "KD-NOVIDEO-77")
        self.assertEqual(e["head"]["note"], "到付快递")
        self.assertEqual(cl.req("GET", "/api/receipts/detail?type=express&id=99999").status, 404)
        self.assertEqual(cl.req("GET", "/api/receipts/detail?type=po&id=99999").status, 404)
        self.assertEqual(cl.req("GET", "/api/receipts/detail?type=../x&id=1").status, 400)

    def test_read_only_connections(self):
        db = self.host.store.connect("po")
        with self.assertRaises(sqlite3.OperationalError):
            db.execute("DELETE FROM session")
        db.close()

    # ---------- 录像 ----------
    def test_video_search_detail_and_index_safety(self):
        cl = self.enable()
        cl.login()
        rows = cl.req("GET", "/api/videos").data["rows"]
        self.assertEqual([r["id"] for r in rows], ["a" * 32])   # 越界 / 链接旁注都不被索引
        self.assertNotIn("_path", json.dumps(rows))
        for q in ("SF1234", "PO-77", "OUT-9", "%E7%A0%B4%E6%8D%9F", "A1"):
            self.assertEqual(len(cl.req("GET", "/api/videos?q=" + q).data["rows"]), 1, q)
        self.assertEqual(len(cl.req("GET", "/api/videos?q=PO-OLD").data["rows"]), 0)   # 以关联索引为准
        # 登记过、没录像的单号（快递单登记）也能在录像查询里搜到；有录像的单号不重复列出
        d = cl.req("GET", "/api/videos?q=novideo-77&from=2000-01-01&to=2000-01-01").data
        self.assertEqual(d["rows"], [])
        self.assertEqual([(r["type"], r["sid"], r["tracking_no"], r["label"]) for r in d["registrations"]],
                         [("express", self.info["esid"], "KD-NOVIDEO-77", "快递单登记")])
        self.assertEqual(cl.req("GET", "/api/videos?q=SF123456789").data["registrations"], [])
        self.assertEqual([r["tracking_no"] for r in cl.req("GET", "/api/videos?q=YT0001").data["registrations"]], ["YT0001"])
        self.assertEqual([r["type"] for r in cl.req("GET", "/api/videos?q=JDJOINT").data["registrations"]], ["joint"])
        self.assertEqual(cl.req("GET", "/api/videos").data["registrations"], [])
        self.assertEqual(len(cl.req("GET", "/api/videos?from=2000-01-01&to=2000-01-01").data["rows"]), 0)
        v = cl.req("GET", "/api/videos/detail?id=" + "a" * 32).data["video"]
        self.assertEqual(v["purchase"], "PO-77")
        self.assertEqual([c["offset"] for c in v["cues"]], [3.2, 8.0])
        self.assertIn("×2", v["cues"][0]["text"])
        self.assertNotIn(self.info["media"], json.dumps(v, ensure_ascii=False))

    def test_video_root_from_native_settings(self):
        other = os.path.join(self.temp.name, "别的录像目录")
        os.makedirs(other)
        with open(os.path.join(self.temp.name, "native-settings.json"), "w", encoding="utf-8") as f:
            json.dump({"Directory": other}, f, ensure_ascii=False)
        self.assertEqual(self.host.videos.root(), os.path.abspath(other))
        self.assertEqual(self.host.videos.items(force=True), [])
        with open(os.path.join(self.temp.name, "native-settings.json"), "w", encoding="utf-8") as f:
            json.dump({"Directory": "relative\\path"}, f)
        self.assertEqual(self.host.videos.root(), os.path.abspath(self.info["root"]))

    def test_path_traversal_rejected(self):
        cl = self.enable()
        cl.login()
        for path in ["/media/../secret.mp4", "/media/%2e%2e%2fsecret.mp4", "/media/..%5c..%5csecret.mp4",
                     "/media/" + "b" * 32 + ".mp4", "/media/" + "c" * 32 + ".mp4", "/media/a.mp4",
                     "/media/C:%5cWindows%5cwin.ini", "/api/videos/detail?id=../../secret",
                     "/api/videos/detail?id=" + "b" * 32, "/assets/../remote_access.py", "/assets/%2e%2e/remote_access.py"]:
            r = cl.req("GET", path)
            self.assertIn(r.status, (400, 404), path)
            self.assertNotIn(b"SECRET", r.raw)
            self.assertNotIn(b"LINKED", r.raw)
            self.assertNotIn(b"import ", r.raw)

    def test_range_streaming(self):
        cl = self.enable()
        cl.login()
        url = "/media/%s.mp4" % ("a" * 32)
        r = cl.req("GET", url)
        self.assertEqual(r.status, 200)
        self.assertEqual(r.raw, VIDEO_BYTES)
        self.assertEqual(r.getheader("Accept-Ranges"), "bytes")
        self.assertEqual(r.getheader("Content-Type"), "video/mp4")
        self.assertEqual(r.getheader("Content-Disposition"), "inline")
        r = cl.req("GET", url, headers={"Range": "bytes=100-199"})
        self.assertEqual(r.status, 206)
        self.assertEqual(r.raw, VIDEO_BYTES[100:200])
        self.assertEqual(r.getheader("Content-Range"), "bytes 100-199/%d" % len(VIDEO_BYTES))
        r = cl.req("GET", url, headers={"Range": "bytes=%d-" % (len(VIDEO_BYTES) - 10)})
        self.assertEqual(r.raw, VIDEO_BYTES[-10:])
        r = cl.req("GET", url, headers={"Range": "bytes=-50"})
        self.assertEqual(r.raw, VIDEO_BYTES[-50:])
        r = cl.req("GET", url, headers={"Range": "bytes=%d-" % (len(VIDEO_BYTES) + 5)})
        self.assertEqual(r.status, 416)
        r = cl.req("HEAD", url)
        self.assertEqual(r.status, 200)
        self.assertEqual(int(r.getheader("Content-Length")), len(VIDEO_BYTES))
        log = self.read_log()
        self.assertIn("观看 录像=" + "a" * 32, log)

    def test_range_parser(self):
        self.assertEqual(_range("bytes=0-0", 10), (0, 0))
        self.assertEqual(_range("bytes=5-100", 10), (5, 9))
        self.assertEqual(_range("bytes=-3", 10), (7, 9))
        self.assertIs(_range("bytes=10-", 10), False)
        self.assertIs(_range("bytes=5-2", 10), False)
        self.assertIsNone(_range("bytes=0-1,4-5", 10))
        self.assertIsNone(_range(None, 10))

    def test_download_blocked_when_disabled(self):
        cl = self.enable()
        cl.login()
        url = "/media/%s.mp4?download=1" % ("a" * 32)
        r = cl.req("GET", url)
        self.assertEqual(r.status, 403)
        self.assertNotEqual(r.raw[:10], VIDEO_BYTES[:10])
        self.assertFalse(cl.req("GET", "/api/me").data["allow_download"])
        self.host.update({"allow_download": True})
        self.assertTrue(self.host.server is not None)
        r = cl.req("GET", url)   # 允许下载不重启监听，会话保持
        self.assertEqual(r.status, 200)
        self.assertIn("attachment", r.getheader("Content-Disposition"))
        self.assertEqual(r.raw, VIDEO_BYTES)

    def test_concurrent_stream_limit(self):
        cl = self.enable()
        cl.login()
        old = remote_access.STREAM_WAIT
        remote_access.STREAM_WAIT = 0.2
        held = [self.host.streams.acquire(timeout=1) for _ in range(remote_access.MAX_STREAMS)]
        try:
            self.assertTrue(all(held))
            r = cl.req("GET", "/media/%s.mp4" % ("a" * 32))
            self.assertEqual(r.status, 503)
        finally:
            for _ in held:
                self.host.streams.release()
            remote_access.STREAM_WAIT = old
        self.assertEqual(cl.req("GET", "/media/%s.mp4" % ("a" * 32), headers={"Range": "bytes=0-9"}).status, 206)

    def test_requests_do_not_need_service_lock(self):
        """远程只读路由在本机业务锁被长时间占用时仍可响应。"""
        cl = self.enable()
        cl.login()
        lock = threading.RLock()
        lock.acquire()   # 模拟 state_lock 被占用：远程路径根本不引用它
        try:
            t = time.time()
            self.assertEqual(cl.req("GET", "/api/receipts").status, 200)
            self.assertLess(time.time() - t, 5)
        finally:
            lock.release()


class GuardTests(unittest.TestCase):
    def test_ipv6_keyed_by_64(self):
        self.assertEqual(ip_key("2408:8000:1:2::5"), ip_key("2408:8000:1:2:ffff::1"))
        self.assertNotEqual(ip_key("2408:8000:1:2::5"), ip_key("2408:8000:1:3::5"))
        self.assertEqual(ip_key("::ffff:1.2.3.4"), "1.2.3.4")

    def test_global_throttle(self):
        g = Guard()
        now = time.time()
        for i in range(remote_access.GLOBAL_FAIL_LIMIT):
            key = "2001:db8:%x::/64" % i
            self.assertIsNone(g.admit(key, now))
            g.failed(key, now)
        self.assertIsNotNone(g.admit("2001:db8:ffff::/64", now + 1))
        self.assertIsNone(g.admit("2001:db8:ffff::/64", now + remote_access.GLOBAL_LOCK_SECONDS + 2))

    def test_per_source_lock_expires(self):
        g = Guard()
        now = time.time()
        for _ in range(remote_access.FAIL_LIMIT):
            g.admit("k", now)
            g.failed("k", now)
        self.assertIsNotNone(g.admit("k", now + 10))
        self.assertIsNone(g.admit("other", now + 10))
        self.assertIsNone(g.admit("k", now + remote_access.LOCK_SECONDS + 61))

    def test_attempt_rate(self):
        g = Guard()
        now = time.time()
        for _ in range(remote_access.ATTEMPTS_PER_MIN):
            self.assertIsNone(g.admit("r", now))
        self.assertIsNotNone(g.admit("r", now))


@unittest.skipUnless(ipv6_ok(), "IPv6 loopback ::1 unavailable")
class ServerIntegrationTests(unittest.TestCase):
    """本机服务上的 /api/remote/*：仅本机地址可用；远程监听器不暴露本机 /api。"""

    def test_local_settings_api(self):
        from inbound_http_test import Fixture
        f = Fixture()
        host = None
        try:
            s = f.server
            host = RemoteHost(f.temp.name, receipt_db=os.path.join(f.temp.name, "fixture.db"))
            s.REMOTE = host
            st = f.call("/api/remote/status")
            self.assertTrue(st["ok"], st)
            self.assertFalse(st["enabled"])
            self.assertFalse(st["running"])
            self.assertEqual(st["port"], remote_access.DEFAULT_PORT)
            port = free_port()
            body = {"enabled": True, "port": port, "username": "admin", "password": PASSWORD}
            bad = f.call("/api/remote/config", body, "https://evil.example")
            self.assertFalse(bad["ok"])
            self.assertIsNone(host.server)
            ok = f.call("/api/remote/config", body, f.base)
            self.assertTrue(ok["ok"], ok)
            self.assertTrue(ok["running"])
            self.assertNotIn("password", json.dumps({k: v for k, v in ok.items() if k != "has_password"}))
            cl = Client(port)
            self.assertEqual(cl.login().status, 200)
            self.assertEqual(cl.req("GET", "/api/state").status, 404)
            rows = cl.req("GET", "/api/receipts?type=po").data["rows"]
            self.assertEqual(rows[0]["po_id"], "123")
            self.assertTrue(f.call("/api/remote/config", {"enabled": False}, f.base)["ok"])
            self.assertFalse(port_open(port))
        finally:
            if host:
                host.stop()
                host.close_log()
            f.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
