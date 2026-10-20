# -*- coding: utf-8 -*-
"""远程管理页（IPv6 直连，只读）。

设计要点（安全边界）：
* 独立监听器，默认关闭；只有在电脑端设置里启用并设好管理员账号密码后才绑定 [::]:端口（IPV6_V6ONLY）。
* 强制 HTTPS：每台电脑首次启用时生成自签名证书，指纹在设置页显示供核对浏览器警告。
* 独立的最小只读路由：不继承本机服务的 Handler，永远到不了 ERP/入库/删除/剪贴板/PDA/设置等 /api/*。
* 只读数据：每个请求自己开 SQLite 只读连接（mode=ro + query_only），不拿 state_lock，不碰共享连接。
* 录像只按索引里的 ID 访问，客户端永远不能传文件路径；路径逐级校验不越出录像根目录、不跟随链接/联接点。
* 登录：scrypt 加盐哈希、常量时间比较、按来源（IPv6 按 /64）限速与锁定、全局暴力破解节流、会话 Cookie
  （__Host- 前缀、Secure、HttpOnly、SameSite=Strict）、空闲 / 绝对超时、登出需 CSRF 令牌；审计日志轮转。
"""
import base64
import collections
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import socket
import socketserver
import sqlite3
import ssl
import stat
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler

DEFAULT_PORT = 18443
IDLE_TIMEOUT = 30 * 60            # 30 分钟无操作退出
ABSOLUTE_TIMEOUT = 12 * 3600      # 登录后最长 12 小时
MAX_SESSIONS = 32
FAIL_WINDOW = 15 * 60
FAIL_LIMIT = 5                    # 同一来源 15 分钟内 5 次失败 → 锁定 15 分钟
LOCK_SECONDS = 15 * 60
ATTEMPTS_PER_MIN = 10             # 同一来源每分钟最多尝试登录次数（成功失败都算）
GLOBAL_FAIL_WINDOW = 10 * 60
GLOBAL_FAIL_LIMIT = 30            # 全局 10 分钟内 30 次失败 → 全部登录暂停
GLOBAL_LOCK_SECONDS = 5 * 60
MAX_STREAMS = 3                   # 同时播放 / 下载的录像流
STREAM_WAIT = 5.0
MAX_CONNECTIONS = 48
CHUNK = 64 * 1024
SOCKET_TIMEOUT = 20
LOGIN_BODY_MAX = 4096
MIN_PASSWORD = 10
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}
COOKIE = "__Host-jst_remote"
VIDEO_ID = re.compile(r"^[A-Za-z0-9]{8,64}$")
USERNAME = re.compile(r"^[A-Za-z0-9_.@-]{1,32}$")
SIDECAR_MAX = 1024 * 1024
ASSOC_MAX = 16 * 1024 * 1024
INDEX_TTL = 20.0
TEST_BIND_ENV = "JST_REMOTE_TEST_BIND"   # 仅测试：只接受 ::1

STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/assets/remote.css": ("remote.css", "text/css; charset=utf-8"),
    "/assets/remote.js": ("remote.js", "application/javascript; charset=utf-8"),
    "/assets/icon.svg": ("icon.svg", "image/svg+xml"),
}
PUBLIC_POST = {"/api/login"}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


def _web_dir():
    if getattr(sys, "frozen", False):
        return os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "remote_web")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "remote_web")


# ---------------------------------------------------------------- 密码

def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT["n"], r=SCRYPT["r"], p=SCRYPT["p"],
                        dklen=SCRYPT["dklen"], maxmem=64 * 1024 * 1024)
    return {"algo": "scrypt", "n": SCRYPT["n"], "r": SCRYPT["r"], "p": SCRYPT["p"],
            "salt": base64.b64encode(salt).decode(), "hash": base64.b64encode(dk).decode()}


def verify_password(password, record):
    """常量时间比较；记录缺失时仍做一次等价计算，避免时间差暴露账号是否存在。"""
    try:
        if not record or record.get("algo") != "scrypt":
            raise ValueError
        salt = base64.b64decode(record["salt"])
        expected = base64.b64decode(record["hash"])
        n, r, p = int(record["n"]), int(record["r"]), int(record["p"])
        if not (2 ** 10 <= n <= 2 ** 20 and 1 <= r <= 32 and 1 <= p <= 16):
            raise ValueError
    except Exception:
        hash_password(password or "", b"\0" * 16)
        return False
    dk = hashlib.scrypt((password or "").encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=len(expected),
                        maxmem=128 * 1024 * 1024)
    return hmac.compare_digest(dk, expected)


# ---------------------------------------------------------------- 工具

def _ci(d, key, default=None):
    """System.Text.Json 写 PascalCase；读取时大小写不敏感。"""
    if not isinstance(d, dict):
        return default
    if key in d:
        return d[key]
    low = key.lower()
    for k, v in d.items():
        if isinstance(k, str) and k.lower() == low:
            return v
    return default


def _parse_time(value):
    if not isinstance(value, str) or not value:
        return None
    s = value.strip().replace("Z", "+00:00")
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt


def _is_reparse(path):
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _norm(path):
    return os.path.normcase(os.path.abspath(path))


def inside(root, path):
    """path 必须在 root 之内，且从 root 到 path 的每一级都不是链接 / 联接点。"""
    root_n, path_n = _norm(root), _norm(path)
    if not path_n.startswith(root_n.rstrip(os.sep) + os.sep):
        return False
    walk = os.path.abspath(path)
    stop = os.path.abspath(root)
    while True:
        if _is_reparse(walk):
            return False
        if _norm(walk) == _norm(stop):
            break
        parent = os.path.dirname(walk)
        if parent == walk:
            return False
        walk = parent
    return not _is_reparse(stop)


def ip_key(addr):
    """限速键：IPv6 按 /64（同一用户可随意换后缀地址），IPv4 按单个地址。"""
    try:
        ip = ipaddress.ip_address(str(addr).split("%", 1)[0])
    except ValueError:
        return str(addr)
    if ip.version == 6:
        if ip.ipv4_mapped:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(str(ip) + "/64", strict=False))
    return str(ip)


def global_ipv6_addresses():
    """本机当前的全局单播 IPv6（2000::/3），含临时地址；不发任何数据包。"""
    out = set()
    try:
        for row in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET6):
            out.add(row[4][0].split("%", 1)[0])
    except OSError:
        pass
    # UDP connect 只选路由源地址，不会真正发送数据。
    for probe in ("2400:3200::1", "2001:4860:4860::8888"):
        s = None
        try:
            s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
            s.connect((probe, 53))
            out.add(s.getsockname()[0].split("%", 1)[0])
        except OSError:
            pass
        finally:
            if s:
                s.close()
    result = []
    for a in out:
        try:
            ip = ipaddress.ip_address(a)
        except ValueError:
            continue
        if ip.version == 6 and ip in ipaddress.ip_network("2000::/3") and ip.is_global:
            result.append(str(ip))
    return sorted(set(result))


def firewall_rule_name(port):
    return "收货小助手远程管理页 TCP %d" % int(port)


def firewall_command(port, program=""):
    name = firewall_rule_name(port)
    cmd = ('netsh advfirewall firewall add rule name="%s" dir=in action=allow protocol=TCP localport=%d profile=any'
           % (name, int(port)))
    if program:
        cmd += ' program="%s"' % program
    return cmd


# ---------------------------------------------------------------- 只读数据

class ReadOnlyData:
    """每次查询独立打开只读连接（WAL 下与写入并发安全），不使用 state_lock 与共享连接。"""

    def __init__(self, data_dir, receipt_db=None):
        self.data = data_dir
        self.paths = {
            "po": receipt_db or os.path.join(data_dir, "jst.db"),
            "unplanned": os.path.join(data_dir, "unplanned.db"),
            "joint": os.path.join(data_dir, "joint_receipts.db"),
        }

    def connect(self, kind):
        path = self.paths[kind]
        if not os.path.isfile(path):
            return None
        uri = "file:" + urllib.parse.quote(os.path.abspath(path).replace("\\", "/")) + "?mode=ro"
        db = sqlite3.connect(uri, uri=True, timeout=3, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=1")
        return db

    @staticmethod
    def _tables(db):
        return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def history(self, kind="all", q="", start="", end="", limit=50, offset=0):
        kinds = ["po", "unplanned", "joint"] if kind == "all" else [kind]
        rows = []
        for k in kinds:
            if k not in self.paths:
                raise ValueError("收货类型无效")
            db = self.connect(k)
            if db is None:
                continue
            try:
                rows.extend(self._history(db, k, q, start, end, limit + offset))
            finally:
                db.close()
        rows.sort(key=lambda r: (r.get("started_at") or "", r["id"]), reverse=True)
        return {"rows": rows[offset:offset + limit], "more": len(rows) > offset + limit}

    def _history(self, db, kind, q, start, end, cap):
        tables = self._tables(db)
        if "session" not in tables:
            return []
        where, args = [], []
        join = ""
        if kind == "unplanned":
            if "unplanned_meta" not in tables:
                return []
            join = " JOIN unplanned_meta m ON m.sid=s.id"
            if "joint_group" in tables:
                where.append("s.id NOT IN (SELECT sid FROM joint_group)")
                where.append("s.id NOT IN (SELECT sid FROM joint_stage)")
        elif kind == "joint":
            if "joint_group" not in tables:
                return []
            join = " JOIN joint_group g ON g.sid=s.id"
        if start:
            where.append("(s.started_at>=? OR EXISTS(SELECT 1 FROM batch b WHERE b.session_id=s.id AND b.first_scan_at>=?))")
            args += [start + " 00:00:00"] * 2
        if end:
            where.append("(s.started_at<=? OR EXISTS(SELECT 1 FROM batch b WHERE b.session_id=s.id AND b.first_scan_at<=?))")
            args += [end + " 23:59:59"] * 2
        if q:
            kw = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            fields = ["s.input_no", "s.po_id", "IFNULL(s.outer_po_id,'')", "IFNULL(s.supplier,'')"]
            if kind == "unplanned":
                fields.append("m.note")
            if kind == "joint":
                fields.append("g.mother")
            cond = [f + " LIKE ? ESCAPE '\\'" for f in fields]
            cond.append("EXISTS(SELECT 1 FROM batch b WHERE b.session_id=s.id AND b.tracking_no LIKE ? ESCAPE '\\')")
            cond.append("EXISTS(SELECT 1 FROM line l WHERE l.session_id=s.id AND (IFNULL(l.sku_id,'') LIKE ? ESCAPE '\\' "
                        "OR IFNULL(l.sku_code,'') LIKE ? ESCAPE '\\' OR IFNULL(l.name,'') LIKE ? ESCAPE '\\'))")
            n = len(fields) + 4
            if kind == "joint":
                cond.append("EXISTS(SELECT 1 FROM joint_purchase p WHERE p.group_sid=s.id AND (p.po_id LIKE ? ESCAPE '\\' OR p.head_json LIKE ? ESCAPE '\\'))")
                n += 2
            where.append("(" + " OR ".join(cond) + ")")
            args += [kw] * n
        extra = ", m.note AS note" if kind == "unplanned" else (", g.mother AS mother" if kind == "joint" else "")
        sql = ("SELECT s.id, s.input_no, s.po_id, s.outer_po_id, s.supplier, s.wh_name, s.state, s.started_at, s.ended_at"
               + extra + " FROM session s" + join + (" WHERE " + " AND ".join(where) if where else "")
               + " ORDER BY s.id DESC LIMIT ?")
        out = []
        for r in db.execute(sql, args + [int(cap)]):
            d = dict(r)
            d["type"] = kind
            agg = db.execute("SELECT COUNT(*) c FROM batch WHERE session_id=?", (d["id"],)).fetchone()
            d["tracking_count"] = agg["c"]
            d["trackings"] = [x[0] for x in db.execute(
                "SELECT tracking_no FROM batch WHERE session_id=? ORDER BY id LIMIT 5", (d["id"],))]
            d["total"] = float(db.execute("SELECT COALESCE(SUM(qty),0) FROM line WHERE session_id=?",
                                          (d["id"],)).fetchone()[0] or 0)
            if kind == "joint":
                d["po_ids"] = [x[0] for x in db.execute("SELECT po_id FROM joint_purchase WHERE group_sid=? ORDER BY id", (d["id"],))]
            d["inbound"] = self._inbound_summary(db, [d["id"]] + (self._stage_ids(db, d["id"]) if kind == "joint" else []))
            out.append(d)
        return out

    @staticmethod
    def _stage_ids(db, sid):
        try:
            return [r[0] for r in db.execute("SELECT sid FROM joint_stage WHERE group_sid=? AND cancelled=0", (sid,))]
        except sqlite3.Error:
            return []

    @staticmethod
    def _inbound_jobs(db, sids):
        if not sids:
            return []
        try:
            q = ",".join("?" * len(sids))
            return [dict(r) for r in db.execute(
                "SELECT id, session_id, status, io_id, created_at, updated_at FROM inbound_job WHERE session_id IN (%s) ORDER BY id DESC" % q,
                sids)]
        except sqlite3.Error:
            return []

    def _inbound_summary(self, db, sids):
        jobs = self._inbound_jobs(db, sids)
        return [{"status": j["status"], "io_id": j["io_id"]} for j in jobs]

    def detail(self, kind, sid):
        if kind not in self.paths:
            raise ValueError("收货类型无效")
        sid = int(sid)
        db = self.connect(kind)
        if db is None:
            raise LookupError("找不到该收货记录")
        try:
            tables = self._tables(db)
            s = db.execute("SELECT * FROM session WHERE id=?", (sid,)).fetchone()
            if not s:
                raise LookupError("找不到该收货记录")
            s = dict(s)
            if kind == "joint" and not db.execute("SELECT 1 FROM joint_group WHERE sid=?", (sid,)).fetchone():
                raise LookupError("找不到该联合收货记录")
            if kind == "unplanned":
                if "unplanned_meta" not in tables or not db.execute("SELECT 1 FROM unplanned_meta WHERE sid=?", (sid,)).fetchone():
                    raise LookupError("找不到该无采购单收货记录")
                if "joint_group" in tables and (db.execute("SELECT 1 FROM joint_group WHERE sid=?", (sid,)).fetchone()
                                                or db.execute("SELECT 1 FROM joint_stage WHERE sid=?", (sid,)).fetchone()):
                    raise LookupError("找不到该无采购单收货记录")
            head = {k: s.get(k) for k in ("id", "input_no", "po_id", "outer_po_id", "supplier", "wh_name", "status",
                                          "state", "started_at", "ended_at", "paused_at")}
            if kind == "unplanned":
                m = db.execute("SELECT note FROM unplanned_meta WHERE sid=?", (sid,)).fetchone()
                head["note"] = m["note"] if m else ""
            batches = []
            for b in db.execute("SELECT id, tracking_no, first_scan_at FROM batch WHERE session_id=? ORDER BY id", (sid,)):
                lines = [dict(l) for l in db.execute(
                    "SELECT id, sku_id, sku_code, name, spec, qty, matched, updated_at FROM line WHERE batch_id=? ORDER BY id",
                    (b["id"],))]
                batches.append(dict(b, lines=lines, total=sum(float(l["qty"] or 0) for l in lines)))
            recv = collections.OrderedDict()
            for b in batches:
                for l in b["lines"]:
                    r = recv.setdefault(l["sku_id"] or "", {"sku_id": l["sku_id"], "sku_code": l["sku_code"],
                                                            "name": l["name"], "spec": l["spec"], "recv": 0.0})
                    r["recv"] += float(l["qty"] or 0)
            compare = []
            items = []
            try:
                items = json.loads(s.get("items_json") or "[]")
            except ValueError:
                items = []
            if kind == "po" or (kind == "unplanned" and s.get("po_id")):
                plan = collections.OrderedDict()
                for it in items if isinstance(items, list) else []:
                    sku = str(it.get("sku_id") or "")
                    p = plan.setdefault(sku, {"sku_id": sku, "sku_code": it.get("sku_code"), "name": it.get("name"),
                                              "spec": it.get("spec"), "plan": 0.0})
                    try:
                        p["plan"] += float(it.get("qty") or 0)
                    except (TypeError, ValueError):
                        pass
                for sku in list(plan) + [k for k in recv if k not in plan]:
                    p, r = plan.get(sku), recv.get(sku)
                    got = r["recv"] if r else 0.0
                    want = p["plan"] if p else None
                    if p is None:
                        status = "采购单外"
                    elif abs(got - want) < 1e-9:
                        status = "一致"
                    elif got == 0:
                        status = "未收到"
                    elif got < want:
                        status = "少收 %g" % (want - got)
                    else:
                        status = "多收 %g" % (got - want)
                    src = p or r
                    compare.append({"sku_id": sku, "sku_code": src.get("sku_code"), "name": src.get("name"),
                                    "spec": src.get("spec"), "plan": want, "recv": got, "status": status})
            else:
                compare = [dict(r, plan=None, status="仅实收") for r in recv.values()]
            out = {"type": kind, "head": head, "batches": batches, "compare": compare}
            sids = [sid]
            if kind == "joint":
                g = db.execute("SELECT mother, expected, created FROM joint_group WHERE sid=?", (sid,)).fetchone()
                out["group"] = dict(g) if g else {}
                purchases = []
                for p in db.execute("SELECT id, po_id, head_json FROM joint_purchase WHERE group_sid=? ORDER BY id", (sid,)):
                    try:
                        h = json.loads(p["head_json"] or "{}")
                    except ValueError:
                        h = {}
                    purchases.append({"po_id": p["po_id"], "outer_po_id": h.get("outer_po_id", ""),
                                      "supplier": h.get("supplier_name") or h.get("supplier", "")})
                out["purchases"] = purchases
                stages = []
                for st in db.execute("SELECT sid, mode, created FROM joint_stage WHERE group_sid=? AND cancelled=0 ORDER BY sid", (sid,)):
                    ss = db.execute("SELECT po_id FROM session WHERE id=?", (st["sid"],)).fetchone()
                    total = db.execute("SELECT qty FROM joint_allocation WHERE stage_sid=?", (st["sid"],)).fetchall()
                    stages.append({"sid": st["sid"], "po_id": ss["po_id"] if ss else "", "mode": st["mode"],
                                   "created": st["created"], "total": sum(float(t[0] or 0) for t in total)})
                    sids.append(st["sid"])
                out["stages"] = stages
            # 不返回 error / snapshot_json：可能含 ERP 响应内容。
            out["inbound"] = self._inbound_jobs(db, sids)
            return out
        finally:
            db.close()


# ---------------------------------------------------------------- 录像索引（只读）

class VideoIndex:
    """读 C# 端写的录像旁注（*.mp4.json）和 video-associations.json；只读不写。"""

    def __init__(self, data_dir, root=None):
        self.data = data_dir
        self.fixed_root = root
        self.lock = threading.Lock()
        self.cache = None
        self.cache_at = 0.0
        self.cache_root = None
        self.warnings = []

    def root(self):
        if self.fixed_root:
            return os.path.abspath(self.fixed_root)
        base = os.path.dirname(os.path.abspath(self.data))
        directory = ""
        try:
            path = os.path.join(base, "native-settings.json")
            if os.path.isfile(path) and os.path.getsize(path) < 1024 * 1024:
                with open(path, encoding="utf-8-sig") as f:
                    directory = _ci(json.load(f), "Directory", "") or ""
        except (OSError, ValueError):
            directory = ""
        if not isinstance(directory, str) or not directory or not os.path.isabs(directory) or "\0" in directory:
            directory = os.path.join(base, "录像")
        return os.path.abspath(directory)

    def _associations(self, root):
        path = os.path.join(root, "video-associations.json")
        try:
            if not os.path.isfile(path) or _is_reparse(path) or os.path.getsize(path) > ASSOC_MAX:
                return {}
            with open(path, encoding="utf-8-sig") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            self.warnings.append("关联索引读取失败")
            return {}

    def _read(self, root, sidecar, assoc):
        if os.path.getsize(sidecar) > SIDECAR_MAX:
            raise ValueError("索引过大")
        with open(sidecar, encoding="utf-8-sig") as f:
            s = json.load(f)
        vid = str(_ci(s, "Id", "") or "")
        file = _ci(s, "File", "")
        if not VIDEO_ID.match(vid) or not isinstance(file, str) or not file.lower().endswith(".mp4"):
            raise ValueError("索引无效")
        # 旁注里的 File 是 Windows 绝对路径；必须与旁注本身同名同目录，才把它当作这份录像。
        if _norm(file) != _norm(sidecar[:-5]):
            raise ValueError("索引路径不匹配")
        media = sidecar[:-5]
        if not inside(root, media):
            raise ValueError("路径越界")
        ctx = _ci(s, "Context", {}) or {}
        source = str(_ci(ctx, "Source", "") or "")
        purchases = _ci(ctx, "Purchases", []) or []
        key = "%s:%s" % (source, _ci(ctx, "SessionId", 0))
        current = assoc.get(key)
        if isinstance(current, dict) and isinstance(_ci(current, "Purchases"), list):
            purchases = _ci(current, "Purchases")
        pos = [str(_ci(p, "Po", "") or "") for p in purchases if isinstance(p, dict)]
        outers = [str(_ci(p, "Outer", "") or "") for p in purchases if isinstance(p, dict)]
        requested = _ci(s, "RequestedAt", "")
        first = _ci(s, "FirstFrameAt", "")
        try:
            size = os.path.getsize(media) if os.path.isfile(media) else 0
        except OSError:
            size = 0
        item = {
            "id": vid,
            "tracking": str(_ci(s, "Tracking", "") or _ci(ctx, "Tracking", "") or ""),
            "purchase": " / ".join(p for p in pos if p),
            "outer": " / ".join(o if o.strip() else "无外部单号" for o in outers) if pos else "",
            "note": str(_ci(s, "Note", "") or ""),
            "state": str(_ci(s, "State", "") or ""),
            "protected": bool(_ci(s, "Protected", False)),
            "source": source,
            "last_sku": str(_ci(ctx, "LastSku", "") or ""),
            "batch_key": str(_ci(s, "BatchKey", "") or ""),
            "at": requested if isinstance(requested, str) else "",
            "end": _ci(s, "EndAt", "") or "",
            "duration": float(_ci(s, "Duration", 0) or 0),
            "bytes": size,
            "cues": self._cues(s, first or requested),
        }
        item["_path"] = media
        item["_sidecar"] = sidecar
        return item

    @staticmethod
    def _cues(s, origin):
        """扫码时间点（若旁注里有）：Cues / Scans / Events，每项 Offset 秒或 At 时间 + 文本。"""
        raw = None
        for name in ("Cues", "Scans", "Events"):
            v = _ci(s, name)
            if isinstance(v, list):
                raw = v
                break
        if not raw:
            return []
        base = _parse_time(origin) if isinstance(origin, str) else None
        out = []
        for c in raw[:500]:
            if not isinstance(c, dict):
                continue
            off = _ci(c, "Offset", _ci(c, "Seconds"))
            if not isinstance(off, (int, float)) or isinstance(off, bool):
                at = _parse_time(_ci(c, "At", ""))
                if at is None or base is None:
                    continue
                try:
                    off = (at - base).total_seconds()
                except TypeError:
                    continue
            if off < 0 or off != off:
                continue
            text = _ci(c, "Text", None) or _ci(c, "Label", None) or _ci(c, "Sku", None) or _ci(c, "Code", None) or ""
            qty = _ci(c, "Quantity", _ci(c, "Qty"))
            label = str(text)[:120]
            if isinstance(qty, (int, float)) and not isinstance(qty, bool):
                label += " ×%g" % qty
            out.append({"offset": round(float(off), 2), "text": label})
        out.sort(key=lambda x: x["offset"])
        return out

    def items(self, force=False):
        root = self.root()
        with self.lock:
            now = time.monotonic()
            if not force and self.cache is not None and self.cache_root == root and now - self.cache_at < INDEX_TTL:
                return self.cache
            self.warnings = []
            result = []
            if os.path.isdir(root) and not _is_reparse(root):
                assoc = self._associations(root)
                for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
                    dirnames[:] = [d for d in dirnames if not _is_reparse(os.path.join(dirpath, d))]
                    for name in filenames:
                        if not name.lower().endswith(".mp4.json"):
                            continue
                        p = os.path.join(dirpath, name)
                        try:
                            if _is_reparse(p):
                                continue
                            result.append(self._read(root, p, assoc))
                        except (OSError, ValueError, TypeError):
                            self.warnings.append(name)
            result.sort(key=lambda x: x["at"], reverse=True)
            seen, unique = set(), []
            for it in result:
                if it["id"] in seen:
                    continue
                seen.add(it["id"])
                unique.append(it)
            self.cache, self.cache_at, self.cache_root = unique, now, root
            return unique

    @staticmethod
    def public(item):
        return {k: v for k, v in item.items() if not k.startswith("_")}

    def search(self, q="", start="", end="", limit=100, offset=0):
        q = (q or "").strip().lower()
        out = []
        for it in self.items():
            if q and not any(q in str(it[k]).lower() for k in ("tracking", "purchase", "outer", "note", "last_sku", "batch_key")):
                continue
            day = (it["at"] or "")[:10]
            if start and day < start:
                continue
            if end and day > end:
                continue
            out.append(it)
        return {"rows": [dict(self.public(x), cues=len(x["cues"])) for x in out[offset:offset + limit]],
                "more": len(out) > offset + limit, "total": len(out)}

    def get(self, vid):
        if not VIDEO_ID.match(vid or ""):
            return None
        for it in self.items():
            if it["id"] == vid:
                return it
        # 刚生成的录像：缓存过期前强制刷新一次。
        for it in self.items(force=True):
            if it["id"] == vid:
                return it
        return None

    def media_path(self, item):
        """播放前再次校验：仍在根目录内、不是链接、文件存在。"""
        root = self.root()
        path = item["_path"]
        if not inside(root, path) or not os.path.isfile(path) or _is_reparse(path):
            return None
        return path


# ---------------------------------------------------------------- 认证与限速

class Guard:
    def __init__(self):
        self.lock = threading.Lock()
        self.sessions = {}
        self.fails = {}          # key -> [timestamps]
        self.attempts = {}       # key -> [timestamps]
        self.locked = {}         # key -> until
        self.global_fails = collections.deque()
        self.global_until = 0.0
        self.verify_slots = threading.BoundedSemaphore(2)

    def _prune(self, now):
        self.fails = {k: [t for t in v if t > now - FAIL_WINDOW] for k, v in self.fails.items()}
        self.fails = {k: v for k, v in self.fails.items() if v}
        self.attempts = {k: [t for t in v if t > now - 60] for k, v in self.attempts.items()}
        self.attempts = {k: v for k, v in self.attempts.items() if v}
        self.locked = {k: v for k, v in self.locked.items() if v > now}
        while self.global_fails and self.global_fails[0] <= now - GLOBAL_FAIL_WINDOW:
            self.global_fails.popleft()
        self.sessions = {k: v for k, v in self.sessions.items()
                         if v["last"] > now - IDLE_TIMEOUT and v["created"] > now - ABSOLUTE_TIMEOUT}

    def admit(self, key, now=None):
        """登录前检查；返回 None 表示可以校验密码，否则返回拒绝原因。"""
        now = time.time() if now is None else now
        with self.lock:
            self._prune(now)
            if self.global_until > now:
                return "登录尝试过多，远程登录已暂时暂停，请稍后再试"
            if self.locked.get(key, 0) > now:
                return "失败次数过多，该网络已锁定15分钟"
            mine = self.attempts.setdefault(key, [])
            if len(mine) >= ATTEMPTS_PER_MIN:
                return "登录请求过于频繁，请一分钟后再试"
            mine.append(now)
            return None

    def failed(self, key, now=None):
        now = time.time() if now is None else now
        with self.lock:
            self._prune(now)
            mine = self.fails.setdefault(key, [])
            mine.append(now)
            self.global_fails.append(now)
            locked = False
            if len(mine) >= FAIL_LIMIT:
                self.locked[key] = now + LOCK_SECONDS
                self.fails.pop(key, None)
                locked = True
            if len(self.global_fails) >= GLOBAL_FAIL_LIMIT:
                self.global_until = now + GLOBAL_LOCK_SECONDS
                self.global_fails.clear()
                locked = True
            return locked

    def succeeded(self, key):
        with self.lock:
            self.fails.pop(key, None)

    def create(self, user, generation, ip, now=None):
        now = time.time() if now is None else now
        token = secrets.token_urlsafe(32)
        with self.lock:
            self._prune(now)
            if len(self.sessions) >= MAX_SESSIONS:
                oldest = min(self.sessions, key=lambda k: self.sessions[k]["last"])
                self.sessions.pop(oldest, None)
            self.sessions[hashlib.sha256(token.encode()).hexdigest()] = {
                "user": user, "generation": generation, "created": now, "last": now,
                "csrf": secrets.token_urlsafe(24), "ip": ip}
        return token

    def lookup(self, token, generation, now=None, touch=True):
        if not token:
            return None
        now = time.time() if now is None else now
        h = hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            self._prune(now)
            s = self.sessions.get(h)
            if not s or s["generation"] != generation:
                self.sessions.pop(h, None)
                return None
            if touch:
                s["last"] = now
            return dict(s, key=h)

    def drop(self, key):
        with self.lock:
            self.sessions.pop(key, None)

    def clear(self):
        with self.lock:
            self.sessions = {}


# ---------------------------------------------------------------- HTTP 服务

class RemoteServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6
    daemon_threads = True
    request_queue_size = 64
    allow_reuse_address = os.name != "nt"

    def __init__(self, addr, handler, host):
        self.host = host
        self.slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(addr, handler, bind_and_activate=False)
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        except (OSError, AttributeError):
            pass
        # 不设 SO_EXCLUSIVEADDRUSE：改密码后需在同一端口立即重启，而独占模式下服务端 TIME_WAIT 会挡住重新绑定。
        try:
            for attempt in range(6):
                try:
                    self.server_bind()
                    break
                except OSError:
                    if attempt == 5:
                        raise
                    time.sleep(0.3)
            self.server_activate()
        except Exception:
            self.server_close()
            raise

    def server_bind(self):
        # 不调用 HTTPServer.server_bind：它会对 "::" 做反向解析（getfqdn），在部分 Windows 网络上很慢。
        socketserver.TCPServer.server_bind(self)
        self.server_name = "remote"
        self.server_port = self.server_address[1]

    def get_request(self):
        sock, addr = super().get_request()
        sock.settimeout(SOCKET_TIMEOUT)
        return sock, addr

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        # TLS 握手失败、客户端中断等：不打印到控制台。
        pass


class RemoteHost:
    def __init__(self, data_dir, receipt_db=None, video_root=None, service_port=None):
        self.data = data_dir
        self.dir = os.path.join(data_dir, "remote-access")
        self.config_path = os.path.join(self.dir, "config.json")
        self.store = ReadOnlyData(data_dir, receipt_db)
        self.videos = VideoIndex(data_dir, video_root)
        self.guard = Guard()
        self.lock = threading.RLock()
        self.server = None
        self.thread = None
        self.error = ""
        self.fingerprint = ""
        self.bound = ""
        self.streams = threading.BoundedSemaphore(MAX_STREAMS)
        self.viewed = {}
        self.service_port = service_port
        self.audit = logging.getLogger("remote.audit.%d" % id(self))
        self.audit.setLevel(logging.INFO)
        self.audit.propagate = False
        self._audit_ready = False

    # ---------- 配置 ----------
    def config(self):
        try:
            with open(self.config_path, encoding="utf-8") as f:
                c = json.load(f)
            if not isinstance(c, dict):
                c = {}
        except (OSError, ValueError):
            c = {}
        port = c.get("port", DEFAULT_PORT)
        return {"enabled": bool(c.get("enabled", False)),
                "port": port if isinstance(port, int) and 1024 <= port <= 65535 else DEFAULT_PORT,
                "username": c.get("username", "") if isinstance(c.get("username"), str) else "",
                "password": c.get("password") if isinstance(c.get("password"), dict) else None,
                "allow_download": bool(c.get("allow_download", False)),
                "generation": c.get("generation", "") if isinstance(c.get("generation"), str) else ""}

    def _write_config(self, c):
        os.makedirs(self.dir, exist_ok=True)
        tmp = self.config_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.config_path)

    def update(self, body):
        """电脑端设置页保存。返回新状态；失败抛 ValueError（已保存的配置保持不变）。"""
        if not isinstance(body, dict):
            raise ValueError("设置格式无效")
        with self.lock:
            c = self.config()
            old = dict(c)
            enabled = body.get("enabled", c["enabled"])
            if not isinstance(enabled, bool):
                raise ValueError("启用开关格式无效")
            allow = body.get("allow_download", c["allow_download"])
            if not isinstance(allow, bool):
                raise ValueError("允许下载开关格式无效")
            port = body.get("port", c["port"])
            try:
                port = int(port)
            except (TypeError, ValueError):
                raise ValueError("端口必须是1024到65535的整数")
            if isinstance(body.get("port"), bool) or not 1024 <= port <= 65535:
                raise ValueError("端口必须是1024到65535的整数")
            if self.service_port and port == self.service_port:
                raise ValueError("端口与本机服务端口相同，请换一个高位端口")
            username = body.get("username", c["username"])
            if not isinstance(username, str) or not USERNAME.match(username.strip()):
                raise ValueError("管理员用户名需为1～32位字母、数字或 _ . @ -")
            username = username.strip()
            password = body.get("password") or ""
            if not isinstance(password, str):
                raise ValueError("密码格式无效")
            record = c["password"]
            if password:
                if len(password) < MIN_PASSWORD or len(password) > 128:
                    raise ValueError("密码至少%d位（最多128位），请使用不易猜到的长密码" % MIN_PASSWORD)
                if password.lower() == username.lower() or len(set(password)) < 4:
                    raise ValueError("密码过于简单，请勿与用户名相同或使用重复字符")
                record = hash_password(password)
            if enabled and not record:
                raise ValueError("启用前请先设置管理员密码")
            generation = c["generation"] or secrets.token_hex(8)
            if password or username != c["username"]:
                generation = secrets.token_hex(8)
            new = {"enabled": enabled, "port": port, "username": username, "password": record,
                   "allow_download": allow, "generation": generation}
            self._write_config(new)
            if generation != c["generation"]:
                self.guard.clear()
            changes = [k for k in ("enabled", "port", "username", "allow_download") if old[k] != new[k]]
            if password:
                changes.append("password")
            self.log("设置已更新：%s" % (",".join(changes) or "无变化"))
            restart = old["enabled"] != enabled or old["port"] != port or bool(password) or username != old["username"]
            if restart or (enabled and not self.server):
                self.stop()
                if enabled:
                    self.start()
            return self.status()

    # ---------- 审计 ----------
    def log(self, msg, ip=""):
        if not self._audit_ready:
            try:
                os.makedirs(os.path.join(self.data, "logs"), exist_ok=True)
                h = RotatingFileHandler(os.path.join(self.data, "logs", "remote-access.log"),
                                        maxBytes=1024 * 1024, backupCount=5, encoding="utf-8")
                h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
                self.audit.addHandler(h)
            except OSError:
                self.audit.addHandler(logging.NullHandler())
            self._audit_ready = True
        clean = str(msg).replace("\r", " ").replace("\n", " ")[:500]
        self.audit.info("%s%s", ("[" + ip + "] ") if ip else "", clean)

    def close_log(self):
        for h in list(self.audit.handlers):
            try:
                h.close()
            except Exception:
                pass
            self.audit.removeHandler(h)
        self._audit_ready = False

    # ---------- 证书 ----------
    def certificate(self):
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        os.makedirs(self.dir, exist_ok=True)
        certfile, keyfile = os.path.join(self.dir, "remote-cert.pem"), os.path.join(self.dir, "remote-key.pem")
        cert = None
        if os.path.isfile(certfile) and os.path.isfile(keyfile):
            try:
                with open(certfile, "rb") as f:
                    cert = x509.load_pem_x509_certificate(f.read())
                if cert.not_valid_after_utc < datetime.now(timezone.utc) + timedelta(days=7):
                    cert = None
            except ValueError:
                cert = None
        if cert is None:
            key = ec.generate_private_key(ec.SECP256R1())
            subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Receiving Assistant remote " + secrets.token_hex(4))])
            now = datetime.now(timezone.utc)
            names = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("::1"))]
            for a in global_ipv6_addresses():
                names.append(x509.IPAddress(ipaddress.ip_address(a)))
            cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
                    .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
                    .not_valid_after(now + timedelta(days=825))
                    .add_extension(x509.SubjectAlternativeName(names), critical=False)
                    .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                    .sign(key, hashes.SHA256()))
            tmpk, tmpc = keyfile + ".tmp", certfile + ".tmp"
            with open(tmpk, "wb") as f:
                f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
            with open(tmpc, "wb") as f:
                f.write(cert.public_bytes(serialization.Encoding.PEM))
            os.replace(tmpk, keyfile)
            os.replace(tmpc, certfile)
            self.log("已生成新的自签名证书")
        fp = cert.fingerprint(hashes.SHA256()).hex().upper()
        self.fingerprint = ":".join(fp[i:i + 2] for i in range(0, len(fp), 2))
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(certfile, keyfile)
        return ctx

    def peek_fingerprint(self):
        if self.fingerprint:
            return self.fingerprint
        certfile = os.path.join(self.dir, "remote-cert.pem")
        try:
            from cryptography import x509
            from cryptography.hazmat.primitives import hashes
            with open(certfile, "rb") as f:
                fp = x509.load_pem_x509_certificate(f.read()).fingerprint(hashes.SHA256()).hex().upper()
            return ":".join(fp[i:i + 2] for i in range(0, len(fp), 2))
        except Exception:
            return ""

    # ---------- 启停 ----------
    def bind_host(self):
        test = os.environ.get(TEST_BIND_ENV, "")
        return "::1" if test == "::1" else "::"

    def start(self):
        with self.lock:
            if self.server:
                return self.status()
            c = self.config()
            if not c["enabled"] or not c["password"] or not c["username"]:
                return self.status()
            try:
                ctx = self.certificate()
                srv = RemoteServer((self.bind_host(), c["port"], 0, 0), make_handler(self), self)
                srv.socket = ctx.wrap_socket(srv.socket, server_side=True, do_handshake_on_connect=False)
            except Exception as e:
                self.error = "远程管理页未能启动：%s" % e
                self.log(self.error)
                return self.status()
            self.server = srv
            self.error = ""
            self.bound = "[%s]:%d" % (srv.server_address[0], srv.server_address[1])
            self.thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.3},
                                           daemon=True, name="remote-https")
            self.thread.start()
            self.log("监听已启动 %s" % self.bound)
            return self.status()

    def start_if_enabled(self):
        try:
            if self.config()["enabled"]:
                self.start()
        except Exception as e:
            self.error = "远程管理页未能启动：%s" % e

    def stop(self):
        with self.lock:
            srv, self.server = self.server, None
            self.guard.clear()
            if srv:
                srv.shutdown()
                srv.server_close()
                self.log("监听已停止 %s" % self.bound)
                self.bound = ""

    def port(self):
        return self.server.server_address[1] if self.server else 0

    def status(self):
        c = self.config()
        addrs = global_ipv6_addresses()
        port = self.port() or c["port"]
        program = sys.executable if getattr(sys, "frozen", False) else ""
        return {"enabled": c["enabled"], "running": bool(self.server), "port": port,
                "username": c["username"], "has_password": bool(c["password"]),
                "allow_download": c["allow_download"], "addresses": addrs,
                "urls": ["https://[%s]:%d/" % (a, port) for a in addrs],
                "fingerprint": self.peek_fingerprint(), "error": self.error,
                "firewall_command": firewall_command(port, program),
                "idle_minutes": IDLE_TIMEOUT // 60, "max_streams": MAX_STREAMS}

    def open_firewall(self):
        """弹出 UAC，以管理员身份为当前端口添加入站放行规则（先删同名旧规则）。"""
        if os.name != "nt":
            raise ValueError("仅 Windows 可用")
        c = self.config()
        port = c["port"]
        program = sys.executable if getattr(sys, "frozen", False) else ""
        name = firewall_rule_name(port)
        args = '/c netsh advfirewall firewall delete rule name="%s" >nul 2>&1 & %s' % (name, firewall_command(port, program))
        import ctypes
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", args, None, 0)
        if rc <= 32:
            raise ValueError("未获得管理员授权或启动失败（代码 %s）；也可复制命令在管理员命令提示符中运行" % rc)
        self.log("已请求添加防火墙规则 端口 %d" % port)
        return {"msg": "已请求 Windows 添加放行规则；若出现管理员确认窗口请点“是”", "command": firewall_command(port, program)}


# ---------------------------------------------------------------- 请求处理

def _range(header, size):
    """返回 (start, end) 或 None（不处理/整文件）或 False（不可满足）。只支持单段。"""
    if not header:
        return None
    m = re.match(r"^bytes=(\d*)-(\d*)$", header.strip())
    if not m or (not m.group(1) and not m.group(2)):
        return None if "," in header else False
    a, b = m.group(1), m.group(2)
    if a:
        start = int(a)
        end = int(b) if b else size - 1
        if start >= size or (b and end < start):
            return False
        return start, min(end, size - 1)
    n = int(b)
    if n == 0:
        return False
    return max(0, size - n), size - 1


def make_handler(host):
    web = _web_dir()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "jst-remote"
        sys_version = ""

        def log_message(self, fmt, *args):
            pass

        @property
        def ip(self):
            return str(self.client_address[0]).split("%", 1)[0]

        def headers_common(self, extra=None):
            h = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
                 "Content-Security-Policy": CSP, "Cross-Origin-Opener-Policy": "same-origin",
                 "Cross-Origin-Resource-Policy": "same-origin", "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
                 "Cache-Control": "no-store"}
            h.update(extra or {})
            return h

        def send(self, code, body=b"", ctype="application/json; charset=utf-8", extra=None):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            for k, v in self.headers_common(extra).items():
                self.send_header(k, v)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD" and body:
                self.wfile.write(body)

        def json(self, obj, code=200, extra=None):
            self.send(code, json.dumps(obj, ensure_ascii=False), extra=extra)

        def fail(self, msg, code=400, extra=None, **kw):
            d = {"ok": False, "msg": msg}
            d.update(kw)
            self.json(d, code, extra)

        def cookie_token(self):
            c = SimpleCookie()
            try:
                c.load(self.headers.get("Cookie") or "")
                return c[COOKIE].value
            except Exception:
                return ""

        def session(self, touch=True):
            return host.guard.lookup(self.cookie_token(), host.config()["generation"], touch=touch)

        def same_origin(self):
            hostname = self.headers.get("Host", "")
            origin = self.headers.get("Origin")
            if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
                return False
            if origin is not None and origin != "https://" + hostname:
                return False
            return True

        def read_body(self, limit):
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("不支持分块请求")
            try:
                size = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise ValueError("请求长度无效")
            if size < 0 or size > limit:
                raise ValueError("请求过大")
            end = time.monotonic() + 5
            chunks, got = [], 0
            while got < size:
                left = end - time.monotonic()
                if left <= 0:
                    raise ValueError("请求体接收超时")
                self.connection.settimeout(left)
                chunk = self.rfile.read1(size - got)
                if not chunk:
                    raise ValueError("请求不完整")
                chunks.append(chunk)
                got += len(chunk)
            self.connection.settimeout(SOCKET_TIMEOUT)
            raw = b"".join(chunks)
            if not raw:
                return {}
            if self.headers.get_content_type() != "application/json":
                raise ValueError("需要JSON请求")
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("请求格式错误")
            return value

        # ---------- 分发 ----------
        def do_HEAD(self):
            self.do_GET()

        def do_PUT(self):
            self.method_not_allowed()

        do_DELETE = do_PATCH = do_OPTIONS = do_TRACE = do_CONNECT = do_PUT

        def method_not_allowed(self):
            self.close_connection = True
            self.fail("不支持的请求方法", 405, {"Allow": "GET, HEAD, POST"})

        def do_GET(self):
            try:
                u = urllib.parse.urlsplit(self.path)
                path = u.path
                q = dict(urllib.parse.parse_qsl(u.query, max_num_fields=20))
                if path in STATIC:
                    return self.static(path)
                if path == "/favicon.ico":
                    return self.send(204, b"")
                s = self.session()
                if not s:
                    self.close_connection = True
                    return self.fail("未登录或会话已过期", 401, login=True)
                if path == "/api/me":
                    c = host.config()
                    return self.json({"ok": True, "user": s["user"], "csrf": s["csrf"],
                                      "allow_download": c["allow_download"], "idle_minutes": IDLE_TIMEOUT // 60})
                if path == "/api/receipts":
                    return self.receipts(q)
                if path == "/api/receipts/detail":
                    return self.receipt_detail(q)
                if path == "/api/videos":
                    return self.video_list(q)
                if path == "/api/videos/detail":
                    return self.video_detail(q)
                m = re.match(r"^/media/([A-Za-z0-9]{8,64})\.mp4$", path)
                if m:
                    return self.media(m.group(1), q, s)
                return self.fail("接口不存在", 404)
            except (BrokenPipeError, ConnectionResetError, ssl.SSLError, socket.timeout, TimeoutError):
                self.close_connection = True
            except Exception:
                self.close_connection = True
                try:
                    self.fail("查询失败，请稍后重试", 500)
                except Exception:
                    pass

        def do_POST(self):
            self.close_connection = True
            try:
                path = urllib.parse.urlsplit(self.path).path
                if not self.same_origin():
                    return self.fail("禁止跨站请求", 403)
                if path == "/api/login":
                    return self.login()
                s = self.session()
                if not s:
                    return self.fail("未登录或会话已过期", 401, login=True)
                if path == "/api/logout":
                    token = self.headers.get("X-CSRF-Token", "")
                    if not hmac.compare_digest(token.encode(), s["csrf"].encode()):
                        return self.fail("请求令牌无效，请刷新页面", 403)
                    host.guard.drop(s["key"])
                    host.log("退出登录 用户=%s" % s["user"], self.ip)
                    return self.json({"ok": True}, extra={"Set-Cookie": COOKIE + "=; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=0"})
                return self.fail("接口不存在", 404)
            except (BrokenPipeError, ConnectionResetError, ssl.SSLError, socket.timeout, TimeoutError):
                pass
            except Exception:
                try:
                    self.fail("请求失败", 500)
                except Exception:
                    pass

        # ---------- 页面 ----------
        def static(self, path):
            name, ctype = STATIC[path]
            try:
                with open(os.path.join(web, name), "rb") as f:
                    body = f.read()
            except OSError:
                return self.fail("页面文件缺失", 404)
            self.send(200, body, ctype)

        def login(self):
            key = ip_key(self.ip)
            try:
                body = self.read_body(LOGIN_BODY_MAX)
            except (ValueError, UnicodeError):
                return self.fail("请求格式错误", 400)
            reason = host.guard.admit(key)
            if reason:
                host.log("登录被拒（限速/锁定）", self.ip)
                return self.fail(reason, 429, {"Retry-After": "60"})
            user = body.get("username")
            password = body.get("password")
            if not isinstance(user, str) or not isinstance(password, str) or len(user) > 64 or len(password) > 256:
                user, password = "", ""
            c = host.config()
            with host.guard.verify_slots:
                ok_pw = verify_password(password, c["password"])
            ok_user = hmac.compare_digest(user.strip().encode("utf-8"), c["username"].encode("utf-8"))
            if not (ok_pw and ok_user and c["username"]):
                locked = host.guard.failed(key)
                host.log("登录失败 用户=%s%s" % (user[:32], "（已触发锁定）" if locked else ""), self.ip)
                time.sleep(0.4)
                return self.fail("用户名或密码错误" + ("；失败次数过多，已锁定15分钟" if locked else ""), 401)
            host.guard.succeeded(key)
            token = host.guard.create(c["username"], c["generation"], self.ip)
            host.log("登录成功 用户=%s" % c["username"], self.ip)
            return self.json({"ok": True}, extra={
                "Set-Cookie": COOKIE + "=" + token + "; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=%d" % ABSOLUTE_TIMEOUT})

        # ---------- 数据 ----------
        @staticmethod
        def date(v):
            v = (v or "").strip()
            if not v:
                return ""
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", v):
                raise ValueError("日期格式应为 YYYY-MM-DD")
            return v

        @staticmethod
        def int_arg(v, default, lo, hi):
            try:
                n = int(v)
            except (TypeError, ValueError):
                return default
            return max(lo, min(hi, n))

        def receipts(self, q):
            try:
                kind = q.get("type", "all")
                if kind not in ("all", "po", "unplanned", "joint"):
                    raise ValueError("收货类型无效")
                text = (q.get("q") or "").strip()[:100]
                data = host.store.history(kind, text, self.date(q.get("from")), self.date(q.get("to")),
                                          self.int_arg(q.get("limit"), 50, 1, 200), self.int_arg(q.get("offset"), 0, 0, 100000))
            except ValueError as e:
                return self.fail(str(e))
            except sqlite3.Error:
                return self.fail("本地数据库暂时忙，请稍后重试", 503)
            return self.json(dict(ok=True, **data))

        def receipt_detail(self, q):
            try:
                kind = q.get("type", "")
                sid = int(q.get("id", ""))
                data = host.store.detail(kind, sid)
            except LookupError as e:
                return self.fail(str(e), 404)
            except ValueError:
                return self.fail("参数无效")
            except sqlite3.Error:
                return self.fail("本地数据库暂时忙，请稍后重试", 503)
            return self.json(dict(ok=True, **data))

        def video_list(self, q):
            try:
                text = (q.get("q") or "").strip()[:100]
                data = host.videos.search(text, self.date(q.get("from")), self.date(q.get("to")),
                                          self.int_arg(q.get("limit"), 100, 1, 300), self.int_arg(q.get("offset"), 0, 0, 100000))
            except ValueError as e:
                return self.fail(str(e))
            return self.json(dict(ok=True, allow_download=host.config()["allow_download"], **data))

        def video_detail(self, q):
            item = host.videos.get(q.get("id", ""))
            if not item:
                return self.fail("找不到该录像", 404)
            return self.json({"ok": True, "video": host.videos.public(item), "allow_download": host.config()["allow_download"]})

        def media(self, vid, q, s):
            download = q.get("download") == "1"
            if download and not host.config()["allow_download"]:
                host.log("拒绝下载（未允许） 录像=%s" % vid, self.ip)
                return self.fail("电脑端未允许下载录像，仅可在线播放", 403)
            item = host.videos.get(vid)
            path = host.videos.media_path(item) if item else None
            if not path:
                return self.fail("找不到该录像文件", 404)
            if not host.streams.acquire(timeout=STREAM_WAIT):
                self.close_connection = True
                return self.fail("同时播放的录像过多，请稍后再试", 503, {"Retry-After": "5"})
            try:
                f = open(path, "rb")
            except OSError:
                host.streams.release()
                return self.fail("录像文件暂时无法读取", 503)
            try:
                size = os.fstat(f.fileno()).st_size
                rng = _range(self.headers.get("Range"), size)
                if rng is False:
                    return self.send(416, b"", "text/plain", {"Content-Range": "bytes */%d" % size, "Accept-Ranges": "bytes"})
                start, end = rng if rng else (0, size - 1)
                length = max(0, end - start + 1)
                if start == 0 or download:
                    marker = (s["key"], vid, download)
                    now = time.time()
                    if download or now - host.viewed.get(marker, 0) > 600:
                        host.viewed[marker] = now
                        if len(host.viewed) > 2000:
                            host.viewed.clear()
                        host.log("%s 录像=%s 物流=%s 用户=%s" % ("下载" if download else "观看", vid, item["tracking"][:60], s["user"]), self.ip)
                self.send_response(206 if rng else 200)
                extra = {"Accept-Ranges": "bytes", "Cache-Control": "private, no-store"}
                if download:
                    name = re.sub(r"[^A-Za-z0-9._-]", "_", item["tracking"] or vid)[:80] or vid
                    extra["Content-Disposition"] = "attachment; filename=\"%s.mp4\"; filename*=UTF-8''%s.mp4" % (
                        name, urllib.parse.quote(item["tracking"] or vid))
                else:
                    extra["Content-Disposition"] = "inline"
                for k, v in self.headers_common(extra).items():
                    self.send_header(k, v)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(length))
                if rng:
                    self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
                self.end_headers()
                if self.command == "HEAD":
                    return
                f.seek(start)
                left = length
                self.connection.settimeout(60)
                while left > 0:
                    chunk = f.read(min(CHUNK, left))
                    if not chunk:
                        self.close_connection = True
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            finally:
                f.close()
                host.streams.release()

    return Handler
