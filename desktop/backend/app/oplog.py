# -*- coding: utf-8 -*-
"""操作记录（审计轨迹）：谁 / 哪台设备 / 什么时间 / 做了什么。

独立 SQLite（data/operation_log.db），与收货业务库分开：
* 尽力写入：任何异常都被吞掉并限频记一行服务日志，绝不让收货、改数、入库等业务操作失败；
* 每次写入短连接（打开-插入-提交-关闭），不常驻文件句柄，busy 超时很短，不会长时间阻塞；
* 只用自己的 threading.Lock，从不获取全局 state_lock；调用方即使持有 state_lock 也不会死锁；
* 绝不保存密码、Cookie、令牌、验证码等敏感字段（scrub 按键名过滤，且只接受白名单列）；
* 按保留天数（默认 180 天，可在操作记录页修改）定期清理。
"""
import csv
import io
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime

DB_NAME = "operation_log.db"
DEFAULT_RETENTION_DAYS = 180
MIN_RETENTION_DAYS = 7
MAX_RETENTION_DAYS = 3650
QUERY_LIMIT_MAX = 5000

_LOG = logging.getLogger("inbound.lifecycle")

SCHEMA = """
CREATE TABLE IF NOT EXISTS op_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at REAL NOT NULL,
  at_text TEXT NOT NULL,
  device_kind TEXT NOT NULL,
  device_id TEXT NOT NULL DEFAULT '',
  device_name TEXT NOT NULL DEFAULT '',
  erp_co TEXT NOT NULL DEFAULT '',
  erp_uid TEXT NOT NULL DEFAULT '',
  erp_user TEXT NOT NULL DEFAULT '',
  module TEXT NOT NULL,
  op TEXT NOT NULL,
  result TEXT NOT NULL DEFAULT 'ok',
  source TEXT NOT NULL DEFAULT '',
  sid INTEGER,
  batch_id INTEGER,
  line_id INTEGER,
  po_id TEXT NOT NULL DEFAULT '',
  outer_po_id TEXT NOT NULL DEFAULT '',
  tracking_no TEXT NOT NULL DEFAULT '',
  sku TEXT NOT NULL DEFAULT '',
  code TEXT NOT NULL DEFAULT '',
  qty_before INTEGER,
  qty_after INTEGER,
  summary TEXT NOT NULL DEFAULT '',
  detail_json TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_op_log_at ON op_log(at);
CREATE INDEX IF NOT EXISTS ix_op_log_source ON op_log(source, sid);
CREATE INDEX IF NOT EXISTS ix_op_log_po ON op_log(po_id);
CREATE INDEX IF NOT EXISTS ix_op_log_tracking ON op_log(tracking_no);
"""

MODULES = {
    "receipt": "有采购单收货",
    "unplanned": "无采购单收货",
    "joint": "联合收货",
    "purchase_watch": "采购单跟踪",
    "inbound": "生成入库单",
    "inventory": "库存盘点",
    "settings": "设置",
    "account": "登录与账号",
    "backup": "备份",
    "pda": "PDA",
}

# 操作类型（筛选用），顺序即界面下拉顺序
OPS = {
    "session_create": "创建收货批次",
    "round_new": "开始新一轮",
    "session_resume": "继续收货",
    "session_pause": "暂停 / 暂存",
    "session_close": "结单",
    "session_delete": "删除收货单",
    "tracking_scan": "扫描物流单号",
    "item_scan": "扫码加数",
    "qty_manual": "手工加数",
    "qty_set": "修改数量",
    "line_edit": "修改明细资料",
    "line_delete": "删除明细",
    "batch_delete": "删除物流子批次",
    "batch_rename": "修改物流单号",
    "batch_copy": "复制上一单",
    "purchase_link": "关联 / 更换采购单",
    "allocation": "分配 / 取消分配",
    "package_finish": "标记整件完成",
    "note": "备注 / 范围",
    "watch": "采购单跟踪",
    "inbound_request": "提交入库单请求",
    "inbound_result": "入库单结果",
    "inventory_submit": "库存修改",
    "settings_change": "修改设置",
    "login": "登录",
    "account_switch": "切换账号",
    "logout": "退出登录",
    "account_delete": "删除记住的账号",
    "backup_create": "创建备份",
    "pda_device": "PDA 服务与设备",
}

DEVICE_LABELS = {"desktop": "电脑", "pda": "PDA", "system": "系统"}

# 永不写入的字段名：长词按子串匹配，短词按整词匹配（避免误伤 shipping 之类）
SECRET_KEYS = ("password", "passwd", "pwd", "cookie", "token", "secret", "verify_code",
               "captcha", "authorization", "session_key", "credential", "pair_code", "fingerprint_pin")
SECRET_EXACT = {"pin", "cid", "sms", "otp", "jd3", "vd144", "pass", "auth_code", "sms_code"}

_COLUMNS = ("module", "op", "result", "source", "sid", "batch_id", "line_id", "po_id",
            "outer_po_id", "tracking_no", "sku", "code", "qty_before", "qty_after", "summary")


def is_secret_key(key):
    k = str(key).lower()
    return k in SECRET_EXACT or any(s in k for s in SECRET_KEYS)


def scrub(value, depth=0):
    """递归去掉敏感键；限制深度和长度，防止把整份 ERP 响应写进日志。"""
    if depth > 4:
        return "…"
    if isinstance(value, dict):
        out = {}
        for k, v in list(value.items())[:60]:
            if is_secret_key(k):
                continue
            out[str(k)[:60]] = scrub(v, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [scrub(v, depth + 1) for v in list(value)[:60]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:500]


def _int_or_none(v):
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(round(f))


def _text(v, n=200):
    return "" if v is None else str(v)[:n]


def parse_time(value, end=False):
    """'YYYY-MM-DD' / 'YYYY-MM-DDTHH:MM[:SS]' / 'YYYY-MM-DD HH:MM[:SS]'，按本机本地时间。"""
    v = str(value or "").strip()
    if not v:
        return None
    v = v.replace("T", " ")
    for fmt, day in (("%Y-%m-%d %H:%M:%S", False), ("%Y-%m-%d %H:%M", False), ("%Y-%m-%d", True)):
        try:
            dt = datetime.strptime(v, fmt)
        except ValueError:
            continue
        ts = time.mktime(dt.timetuple())
        if end:
            ts += 86400 if day else (60 if fmt == "%Y-%m-%d %H:%M" else 1)
        return ts
    raise ValueError("时间格式无效：" + v[:40])


class OpLog(object):
    def __init__(self, path_fn, retention_fn=None):
        # path_fn：返回数据库完整路径的函数（每次写入时求值，目录不存在则不写、不创建）。
        self.path_fn = path_fn if callable(path_fn) else (lambda p=path_fn: p)
        self.retention_fn = retention_fn
        self.lock = threading.Lock()
        self.ready_path = None
        self.last_purge = 0.0
        self.failures = 0
        self.last_error = ""
        self._last_log = 0.0
        self.disabled_for_test = False   # 回归测试：模拟写入失败

    # ------------------------------------------------------------ helpers
    def path(self):
        return self.path_fn()

    def retention_days(self):
        try:
            v = int(self.retention_fn()) if self.retention_fn else DEFAULT_RETENTION_DAYS
        except Exception:
            v = DEFAULT_RETENTION_DAYS
        return max(MIN_RETENTION_DAYS, min(MAX_RETENTION_DAYS, v))

    def _connect(self, create):
        p = self.path()
        if not p:
            raise OSError("操作记录路径未配置")
        d = os.path.dirname(p)
        if d and not os.path.isdir(d):
            raise OSError("数据目录不存在")
        if not create and not os.path.isfile(p):
            return None
        db = sqlite3.connect(p, timeout=0.5)
        db.row_factory = sqlite3.Row
        if self.ready_path != p:
            db.executescript(SCHEMA)
            db.commit()
            self.ready_path = p
        return db

    def _note_failure(self, e):
        self.failures += 1
        self.last_error = type(e).__name__ + ": " + str(e)[:200]
        now = time.time()
        if now - self._last_log > 60:
            self._last_log = now
            try:
                _LOG.warning("操作记录写入失败（不影响业务）：%s", self.last_error)
            except Exception:
                pass

    # ------------------------------------------------------------ write
    def build(self, rec, device=None, erp=None):
        """把一条记录规整成列值；未知键进入 detail_json（已去敏）。"""
        rec = dict(rec or {})
        device = device or {}
        erp = erp or {}
        now = rec.pop("at", None) or time.time()
        row = {
            "at": float(now),
            "at_text": datetime.fromtimestamp(float(now)).strftime("%Y-%m-%d %H:%M:%S"),
            "device_kind": _text(device.get("kind") or "desktop", 20),
            "device_id": _text(device.get("id"), 40),
            "device_name": _text(device.get("name"), 60),
            "erp_co": _text(erp.get("co"), 40),
            "erp_uid": _text(erp.get("uid"), 40),
            "erp_user": _text(erp.get("user"), 60),
        }
        detail = rec.pop("detail", None)
        extra = {k: v for k, v in rec.items() if k not in _COLUMNS}
        for k in _COLUMNS:
            v = rec.get(k)
            if k in ("sid", "batch_id", "line_id", "qty_before", "qty_after"):
                row[k] = _int_or_none(v)
            elif k == "summary":
                row[k] = _text(v, 500)
            else:
                row[k] = _text(v)
        row["module"] = row["module"] or "receipt"
        row["op"] = row["op"] or "note"
        row["result"] = row["result"] or "ok"
        if detail is not None:
            extra["detail"] = detail
        row["detail_json"] = json.dumps(scrub(extra), ensure_ascii=False, sort_keys=True) if extra else ""
        return row

    def write(self, records, device=None, erp=None):
        """尽力写入；返回写入条数（失败返回 0，从不抛异常）。"""
        if not records:
            return 0
        try:
            rows = [self.build(r, device, erp) for r in records if r]
            if not rows:
                return 0
            if self.disabled_for_test:
                raise sqlite3.OperationalError("模拟操作记录库不可用")
            with self.lock:
                db = self._connect(True)
                try:
                    cols = list(rows[0].keys())
                    sql = "INSERT INTO op_log(%s) VALUES(%s)" % (",".join(cols), ",".join("?" * len(cols)))
                    with db:
                        db.executemany(sql, [[r[c] for c in cols] for r in rows])
                    if time.time() - self.last_purge > 6 * 3600:
                        self.last_purge = time.time()
                        cutoff = time.time() - self.retention_days() * 86400
                        with db:
                            db.execute("DELETE FROM op_log WHERE at<?", (cutoff,))
                finally:
                    db.close()
            return len(rows)
        except Exception as e:  # noqa: BLE001 - must never break the business operation
            self._note_failure(e)
            return 0

    def purge(self):
        try:
            with self.lock:
                db = self._connect(False)
                if db is None:
                    return 0
                try:
                    cutoff = time.time() - self.retention_days() * 86400
                    with db:
                        n = db.execute("DELETE FROM op_log WHERE at<?", (cutoff,)).rowcount
                    self.last_purge = time.time()
                    return n
                finally:
                    db.close()
        except Exception as e:
            self._note_failure(e)
            return 0

    # ------------------------------------------------------------ read
    def _where(self, f):
        f = f or {}
        where, args = [], []
        start, end = parse_time(f.get("start")), parse_time(f.get("end"), end=True)
        if start is not None:
            where.append("at>=?"); args.append(start)
        if end is not None:
            where.append("at<?"); args.append(end)
        has_sid = bool(str(f.get("sid") or "").strip())
        for key in ("module", "op", "result", "device_kind", "source"):
            v = str(f.get(key) or "").strip()
            if v:
                if key == "source" and has_sid:
                    # 按单查看：来源为空但 sid 相同的旧记录也算本单
                    where.append("(source=? OR source='')"); args.append(v)
                else:
                    where.append(key + "=?"); args.append(v)
        if has_sid:
            where.append("sid=?"); args.append(_int_or_none(f.get("sid")))
        device = str(f.get("device") or "").strip()
        if device:
            if device in DEVICE_LABELS:
                where.append("device_kind=?"); args.append(device)
            else:
                where.append("(device_id=? OR device_name LIKE ?)"); args += [device, "%" + device + "%"]
        for key, cols in (("po", ("po_id", "outer_po_id")), ("tracking", ("tracking_no",)),
                          ("sku", ("sku", "code"))):
            v = str(f.get(key) or "").strip()
            if v:
                where.append("(" + " OR ".join(c + " LIKE ?" for c in cols) + ")")
                args += ["%" + v + "%"] * len(cols)
        text = str(f.get("q") or "").strip()
        if text:
            cols = ("po_id", "outer_po_id", "tracking_no", "sku", "code", "summary", "device_name")
            where.append("(" + " OR ".join(c + " LIKE ?" for c in cols) + ")")
            args += ["%" + text + "%"] * len(cols)
        return (" WHERE " + " AND ".join(where)) if where else "", args

    def query(self, f=None, limit=500, offset=0):
        try:
            limit = max(1, min(QUERY_LIMIT_MAX, int(limit or 500)))
        except (TypeError, ValueError):
            limit = 500
        try:
            offset = max(0, int(offset or 0))
        except (TypeError, ValueError):
            offset = 0
        where, args = self._where(f)
        with self.lock:
            db = self._connect(False)
            if db is None:
                return {"rows": [], "total": 0, "limit": limit, "offset": offset}
            try:
                total = db.execute("SELECT COUNT(*) FROM op_log" + where, args).fetchone()[0]
                rows = [self._present(dict(r)) for r in db.execute(
                    "SELECT * FROM op_log" + where + " ORDER BY id DESC LIMIT ? OFFSET ?", args + [limit, offset])]
            finally:
                db.close()
        return {"rows": rows, "total": total, "limit": limit, "offset": offset}

    def devices(self):
        with self.lock:
            db = self._connect(False)
            if db is None:
                return []
            try:
                return [dict(r) for r in db.execute(
                    "SELECT device_kind, device_id, device_name, MAX(at_text) AS last FROM op_log "
                    "WHERE device_kind='pda' GROUP BY device_id, device_name ORDER BY last DESC LIMIT 100")]
            finally:
                db.close()

    @staticmethod
    def _present(r):
        r["module_label"] = MODULES.get(r["module"], r["module"])
        r["op_label"] = OPS.get(r["op"], r["op"])
        kind = r["device_kind"]
        label = DEVICE_LABELS.get(kind, kind)
        if kind == "pda":
            label = "PDA：" + (r["device_name"] or "未命名") + (" [" + r["device_id"] + "]" if r["device_id"] else "")
        r["device_label"] = label
        try:
            r["detail"] = json.loads(r.pop("detail_json") or "{}")
        except ValueError:
            r["detail"] = {}
        if r["qty_before"] is not None or r["qty_after"] is not None:
            b, a = r["qty_before"] or 0, r["qty_after"] or 0
            r["qty_change"] = a - b
        else:
            r["qty_change"] = None
        return r

    def export_csv(self, f=None):
        data = self.query(f, QUERY_LIMIT_MAX)
        out = io.StringIO(newline="")
        w = csv.writer(out)
        w.writerow(["时间", "设备", "ERP账号", "模块", "操作类型", "结果", "采购单号", "外部单号",
                    "物流单号", "商品编码", "扫描条码", "操作前数量", "操作后数量", "数量变化", "说明", "记录ID"])

        def cell(v):
            v = "" if v is None else str(v)
            return "'" + v if v.lstrip().startswith(("=", "+", "-", "@")) or v.startswith(("\t", "\r", "\n")) else v

        for r in data["rows"]:
            w.writerow([cell(x) for x in (
                r["at_text"], r["device_label"], (r["erp_user"] or r["erp_uid"]),
                r["module_label"], r["op_label"], "成功" if r["result"] == "ok" else ("失败" if r["result"] == "fail" else r["result"]),
                r["po_id"], r["outer_po_id"], r["tracking_no"], r["sku"], r["code"],
                r["qty_before"], r["qty_after"], r["qty_change"], r["summary"], r["id"])])
        return ("\ufeff" + out.getvalue()).encode("utf-8")
