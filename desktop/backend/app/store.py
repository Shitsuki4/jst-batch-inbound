# -*- coding: utf-8 -*-
"""本地 SQLite 数据层 + 差异校验 + 导出（CSV / XLSX）。"""
import json
import math
import os
import sqlite3
import time
from datetime import datetime

DEFAULT_EXPORT_COLS = ["采购单号", "对应物流单号", "扫描时间", "商品编码", "商品名称", "数量"]

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT
);

CREATE TABLE IF NOT EXISTS session (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    input_no     TEXT NOT NULL,
    po_id        TEXT NOT NULL,
    outer_po_id  TEXT,
    id_type      TEXT,
    supplier     TEXT,
    wh_name      TEXT,
    status       TEXT,
    po_date      TEXT,
    owner_co_id  TEXT,
    auth_co_id   TEXT,
    state        TEXT NOT NULL DEFAULT 'active',
    started_at   TEXT,
    ended_at     TEXT,
    paused_at    TEXT,
    items_json   TEXT
);
CREATE INDEX IF NOT EXISTS ix_session_po ON session(po_id);
-- 注意：这里**不能**再建 ux_session_active 唯一索引。
-- 旧版限制「同一时刻只允许一张采购单处于收货状态」，导致一个批次卡很久、别的单子进不来。
-- 现在一张采购单就一张收货单，可随时「退出批次」(state=paused) 去处理别的单，
-- 也可以多张单同时存在（一张 active、其余 paused/closed）。
CREATE INDEX IF NOT EXISTS ix_session_state ON session(state);

CREATE TABLE IF NOT EXISTS batch (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    INTEGER NOT NULL,
    tracking_no   TEXT NOT NULL,
    first_scan_at TEXT NOT NULL,
    UNIQUE(session_id, tracking_no)
);
CREATE INDEX IF NOT EXISTS ix_batch_session ON batch(session_id);
CREATE INDEX IF NOT EXISTS ix_batch_track ON batch(tracking_no);

CREATE TABLE IF NOT EXISTS line (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    batch_id   INTEGER NOT NULL,
    poi_id     TEXT,
    key        TEXT NOT NULL,
    sku_id     TEXT,
    i_id       TEXT,
    sku_code   TEXT,
    name       TEXT,
    spec       TEXT,
    qty        REAL NOT NULL DEFAULT 0,
    matched    INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT,
    UNIQUE(batch_id, key)
);
CREATE INDEX IF NOT EXISTS ix_line_session ON line(session_id);
CREATE INDEX IF NOT EXISTS ix_line_batch ON line(batch_id);

-- 商品资料反查缓存：避免同一个陌生码反复打接口
CREATE TABLE IF NOT EXISTS sku_cache (
    code        TEXT PRIMARY KEY,
    hit         INTEGER NOT NULL DEFAULT 0,
    sku_id      TEXT,
    i_id        TEXT,
    name        TEXT,
    spec        TEXT,
    sku_code    TEXT,
    enabled     TEXT,
    pic         TEXT,
    owner_co_id TEXT,
    updated_at  TEXT
);

-- 常用登录账号：只记账号名方便下次快速选择，绝不存密码
-- 实收入库写入日志；快照先落地，网络超时/重启后也不能重复开单。
CREATE TABLE IF NOT EXISTS inbound_job (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    fingerprint TEXT NOT NULL UNIQUE,
    snapshot_json TEXT NOT NULL,
    login_co_id TEXT NOT NULL,
    status TEXT NOT NULL,
    io_id TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_inbound_session ON inbound_job(session_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_inbound_remote ON inbound_job(login_co_id,io_id) WHERE io_id<>'';
CREATE TABLE IF NOT EXISTS inbound_batch (
    batch_id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL,
    line_scoped INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS inbound_line (
    line_id INTEGER PRIMARY KEY,
    batch_id INTEGER NOT NULL,
    job_id INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_inbound_line_job ON inbound_line(job_id);
-- 数据库层保护已占用明细，包括已生成、创建结果不明和待重试的记录。
CREATE TRIGGER IF NOT EXISTS inbound_line_insert BEFORE INSERT ON line
WHEN EXISTS (SELECT 1 FROM inbound_batch WHERE batch_id=NEW.batch_id)
BEGIN SELECT RAISE(ABORT, '该物流单号已生成或正在生成入库单；补收请新建物流子批次'); END;
CREATE TRIGGER IF NOT EXISTS inbound_line_update BEFORE UPDATE ON line
WHEN EXISTS (SELECT 1 FROM inbound_batch WHERE batch_id IN (OLD.batch_id,NEW.batch_id))
BEGIN SELECT RAISE(ABORT, '已关联入库单的实收明细不可修改，请在聚水潭核对原单'); END;
CREATE TRIGGER IF NOT EXISTS inbound_line_delete BEFORE DELETE ON line
WHEN EXISTS (SELECT 1 FROM inbound_batch WHERE batch_id=OLD.batch_id)
BEGIN SELECT RAISE(ABORT, '已关联入库单的实收明细不可删除'); END;
CREATE TRIGGER IF NOT EXISTS inbound_batch_update BEFORE UPDATE ON batch
WHEN EXISTS (SELECT 1 FROM inbound_batch WHERE batch_id=OLD.id)
BEGIN SELECT RAISE(ABORT, '已关联入库单的物流单号不可修改'); END;
CREATE TRIGGER IF NOT EXISTS inbound_batch_delete BEFORE DELETE ON batch
WHEN EXISTS (SELECT 1 FROM inbound_batch WHERE batch_id=OLD.id)
BEGIN SELECT RAISE(ABORT, '已关联入库单的物流批次不可删除'); END;
CREATE TRIGGER IF NOT EXISTS inbound_session_delete BEFORE DELETE ON session
WHEN EXISTS (SELECT 1 FROM inbound_job WHERE session_id=OLD.id)
BEGIN SELECT RAISE(ABORT, '该收货批次存在入库记录，不可删除'); END;
CREATE TRIGGER IF NOT EXISTS inbound_session_context BEFORE UPDATE OF po_id,owner_co_id,auth_co_id ON session
WHEN EXISTS (SELECT 1 FROM inbound_job WHERE session_id=OLD.id)
 AND (NEW.po_id IS NOT OLD.po_id OR NEW.owner_co_id IS NOT OLD.owner_co_id OR NEW.auth_co_id IS NOT OLD.auth_co_id)
BEGIN SELECT RAISE(ABORT, '该收货批次已关联入库单，不可更换采购单或公司上下文'); END;

CREATE TABLE IF NOT EXISTS receipt_copy_preference (scope TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS receipt_close_snapshot (sid INTEGER PRIMARY KEY, at TEXT NOT NULL, snapshot_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS receipt_copy_log (
    target_bid INTEGER PRIMARY KEY, sid INTEGER NOT NULL, source_bid INTEGER NOT NULL,
    source_tracking TEXT NOT NULL, target_tracking TEXT NOT NULL,
    snapshot_json TEXT NOT NULL, at TEXT NOT NULL
);

-- 前端每次实际扫码/确认生成一个 client_op_id，网络重试时复用；同一个编号只入账一次。
CREATE TABLE IF NOT EXISTS client_op (
    id            TEXT PRIMARY KEY,
    path          TEXT NOT NULL,
    digest        TEXT NOT NULL,
    line_id       INTEGER,
    qty           REAL,
    added         REAL,
    response_json TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS saved_account (
    name         TEXT PRIMARY KEY,
    kind         TEXT,
    last_used_at TEXT
);
"""


def now_str(fmt="%Y-%m-%d %H:%M:%S"):
    return datetime.now().strftime(fmt)


MAX_QTY = 1000000000
QTY_ERROR = "数量必须是整数（商品数量不能有小数），且不能为负数或超过10亿"


def validate_qty(value, allow_zero=False):
    """Shared receipt-quantity validator: whole numbers only, 0 <= n <= 1e9.

    Rejects bool, NaN/inf, negatives, fractions and absurd values. Integral floats
    (3.0) and numeric strings ("3", "3.0") are accepted and returned as int.
    """
    if isinstance(value, bool) or value is None:
        raise ValueError(QTY_ERROR)
    if isinstance(value, int):
        n = value
    else:
        try:
            f = float(value.strip() if isinstance(value, str) else value)
        except (TypeError, ValueError):
            raise ValueError(QTY_ERROR)
        if not math.isfinite(f) or not f.is_integer():
            raise ValueError(QTY_ERROR)
        n = int(f)
    if n < 0 or n > MAX_QTY or (n == 0 and not allow_zero):
        raise ValueError(QTY_ERROR)
    return n


def _plan_qty(value):
    try:
        f = float(value or 0)
        return f if math.isfinite(f) else 0.0
    except (TypeError, ValueError):
        return 0.0


def plan_by_key(items):
    """Purchase lines aggregated per receipt key ("sku:" + sku_id), first-seen order.

    A purchase order can list the same SKU on several lines. Receipts are stored per
    SKU key, so every comparison (diff report, PO summary, left-panel items, the
    "done elsewhere" rows) must compare against the SKU total, like purchase_balance
    and the inbound preview already do."""
    out = {}
    for it in items or []:
        key = "sku:" + str(it.get("sku_id"))
        row = out.get(key)
        if row is None:
            row = out[key] = {"key": key, "sku_id": it.get("sku_id"), "sku_code": it.get("sku_code"),
                              "name": it.get("name"), "spec": it.get("spec"), "plan": 0.0, "lines": []}
        row["plan"] += _plan_qty(it.get("qty"))
        row["lines"].append(it)
    return out


class Store(object):
    def __init__(self, db_path):
        d = os.path.dirname(db_path)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        self.db = sqlite3.connect(db_path, check_same_thread=False, timeout=15)
        self.db.row_factory = sqlite3.Row
        self._drop_legacy_indexes()      # 必须早于建表：先清掉旧约束，否则建表脚本也会被它挡住
        self.db.executescript(SCHEMA)
        self.db.commit()
        self._migrate()
        from purchase_snapshot import migrate
        migrate(self)
        from native_bridge import migrate as native_migrate
        native_migrate(self)

    def get_inbound(self, job_id):
        r = self.db.execute("SELECT * FROM inbound_job WHERE id=?", (job_id,)).fetchone()
        return dict(r) if r else None

    def inbound_jobs(self, sid):
        rows = self.db.execute(
            "SELECT id,session_id,status,io_id,error,created_at,updated_at,snapshot_json FROM inbound_job "
            "WHERE session_id=? ORDER BY id DESC", (sid,)).fetchall()
        return [dict(r) for r in rows]

    def reserve_inbound(self, snapshot, fingerprint, login_co_id):
        ts = now_str()
        with self.db:
            cur = self.db.execute(
                "INSERT INTO inbound_job(session_id,fingerprint,snapshot_json,login_co_id,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (snapshot["session_id"], fingerprint, json.dumps(snapshot, ensure_ascii=False),
                 login_co_id, "creating", ts, ts))
            job_id = cur.lastrowid
            for batch in snapshot["batches"]:
                prior = self.db.execute("SELECT * FROM inbound_batch WHERE batch_id=?", (batch["id"],)).fetchone()
                if prior and not prior["line_scoped"]:
                    raise ValueError("旧版已关联物流批次不可重复生成")
                self.db.execute("INSERT OR IGNORE INTO inbound_batch(batch_id,job_id,line_scoped) VALUES(?,?,1)", (batch["id"], job_id))
                for line in batch["lines"]:
                    self.db.execute("INSERT INTO inbound_line(line_id,batch_id,job_id) VALUES(?,?,?)", (line["id"], batch["id"], job_id))
        return job_id

    def reject_inbound(self, job_id, error):
        """仅用于新建接口明确拒绝；不是网络超时。保留审计记录，允许重新预览。"""
        with self.db:
            self.db.execute("DELETE FROM inbound_line WHERE job_id=?", (job_id,))
            # The first job owns the UI/edit lock. If it is rejected, retain the lock for any other job.
            self.db.execute("UPDATE inbound_batch SET job_id=(SELECT MIN(il.job_id) FROM inbound_line il WHERE il.batch_id=inbound_batch.batch_id) WHERE job_id=? AND line_scoped=1 AND EXISTS(SELECT 1 FROM inbound_line il WHERE il.batch_id=inbound_batch.batch_id)", (job_id,))
            self.db.execute("DELETE FROM inbound_batch WHERE job_id=?", (job_id,))
            self.db.execute("UPDATE inbound_job SET status='rejected',error=?,updated_at=?,"
                            "fingerprint=fingerprint || ':rejected:' || id WHERE id=?",
                            (error, now_str(), job_id))

    def update_inbound(self, job_id, status, io_id=None, error=""):
        with self.db:
            if io_id is None:
                self.db.execute("UPDATE inbound_job SET status=?,error=?,updated_at=? WHERE id=?",
                                (status, error, now_str(), job_id))
            else:
                self.db.execute("UPDATE inbound_job SET status=?,io_id=?,error=?,updated_at=? WHERE id=?",
                                (status, io_id, error, now_str(), job_id))

    def _drop_legacy_indexes(self):
        """旧版用唯一索引 ux_session_active 把「同时只能有一张收货中的单」写死在库里。
        新版允许多张单并存（一 active、其余 paused），这个索引必须去掉，否则第二张单插不进去。

        另外 SQLite 里 UPDATE 多行时唯一索引是**逐行校验**的：把 paused 的单改回 active
        那一刻如果还有别的 active 行，UPDATE 会直接 IntegrityError。所以这里一定要删干净。
        """
        try:
            idx = self.db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='ux_session_active'").fetchone()
            if idx:
                self.db.execute("DROP INDEX ux_session_active")
                self.db.commit()
        except Exception:
            pass

    def _migrate(self):
        """轻量迁移：旧记录不回填猜测的收货基线。"""
        try:
            with self.db:   # 去重记录只需覆盖网络重试窗口，保留 30 天足够
                self.db.execute("DELETE FROM client_op WHERE created_at < datetime('now','localtime','-30 days')")
        except sqlite3.Error:
            pass
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(inbound_batch)")}
        if "line_scoped" not in cols:
            self.db.execute("ALTER TABLE inbound_batch ADD COLUMN line_scoped INTEGER NOT NULL DEFAULT 0")
            self.db.commit()
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(session)")}
        if "round_baseline_json" not in cols:
            self.db.execute("ALTER TABLE session ADD COLUMN round_baseline_json TEXT")
            self.db.commit()
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(session)").fetchall()}
        if "paused_at" not in cols:
            try:
                self.db.execute("ALTER TABLE session ADD COLUMN paused_at TEXT")
                self.db.commit()
            except Exception:
                pass
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(sku_cache)").fetchall()}
        if "pic" not in cols:
            try:
                self.db.execute("ALTER TABLE sku_cache ADD COLUMN pic TEXT")
                self.db.commit()
            except Exception:
                pass

    # ---------------- 设置 ----------------
    def get_kv(self, k, default=None):
        r = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default

    def set_kv(self, k, v):
        with self.db:
            self.db.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))

    def set_many(self, d):
        with self.db:
            for k, v in d.items():
                self.db.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))

    # ---------------- 常用登录账号 ----------------
    def save_account(self, name, kind="pw"):
        """登录成功后记下账号名（只记账号，绝不存密码），供登录页快速选择。"""
        name = (name or "").strip()
        if not name:
            return
        with self.db:
            self.db.execute(
                "INSERT INTO saved_account(name,kind,last_used_at) VALUES(?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET kind=excluded.kind, last_used_at=excluded.last_used_at",
                (name, kind, now_str()))

    def list_accounts(self, limit=8):
        rows = self.db.execute(
            "SELECT name, kind, last_used_at FROM saved_account ORDER BY last_used_at DESC LIMIT ?",
            (limit,)).fetchall()
        return [dict(r) for r in rows]

    def del_account(self, name):
        with self.db:
            self.db.execute("DELETE FROM saved_account WHERE name=?", ((name or "").strip(),))

    # ---------------- 采购单收货单 ----------------
    # state 语义：
    #   active —— 当前正在收货（同一时刻最多一张，由 UI 保证，库里不再强约束）
    #   paused —— 「退出批次」后挂起，数据保留，点一下可继续扫
    #   closed —— 已「结单」，只读，仍可在记录查询里改数量/删除
    def active_session(self):
        r = self.db.execute("SELECT * FROM session WHERE state='active' ORDER BY id DESC LIMIT 1").fetchone()
        return dict(r) if r else None

    def get_session(self, sid):
        r = self.db.execute("SELECT * FROM session WHERE id=?", (sid,)).fetchone()
        return dict(r) if r else None

    def find_session_by_po(self, po_id, owner=None, auth=None):
        """Prefer the unfinished round, within exact company scope when supplied."""
        sql = "SELECT * FROM session WHERE po_id=?"
        args = [po_id]
        if owner is not None:
            sql += " AND COALESCE(owner_co_id,'')=?"
            args.append(str(owner or ""))
        if auth is not None:
            sql += " AND COALESCE(auth_co_id,'')=?"
            args.append(str(auth or ""))
        r = self.db.execute(sql + " ORDER BY (state='closed'), id DESC LIMIT 1", args).fetchone()
        return dict(r) if r else None

    def open_sessions(self):
        """所有还没结单的收货单（收货中 + 已挂起），新的在前"""
        rows = self.db.execute(
            "SELECT * FROM session WHERE state IN ('active','paused') ORDER BY (state='paused'), id DESC").fetchall()
        return [dict(r) for r in rows]

    def open_session(self, input_no, po_id, id_type, head, items):
        """同公司未结束的轮次复用；已结单则新建，绝不混入历史收货。"""
        old = self.find_session_by_po(po_id, head.get("owner_co_id", ""), head.get("auth_co_id", ""))
        ts = now_str()
        from purchase_snapshot import capture
        with self.db:   # snapshot capture + session write are one atomic unit
            capture(self, dict(head, po_id=po_id), json.dumps(items, ensure_ascii=False), ts, 'first_query')
            if old and old["state"] != "closed":
                self.db.execute(
                    "UPDATE session SET input_no=?, id_type=?, supplier=?, wh_name=?, status=?, po_date=?,"
                    " owner_co_id=?, auth_co_id=?, outer_po_id=?, items_json=? WHERE id=?",
                    (input_no, id_type, head.get("supplier", ""), head.get("wh_name", ""),
                     head.get("status", ""), head.get("po_date", ""), head.get("owner_co_id", ""),
                     head.get("auth_co_id", ""), head.get("outer_po_id", ""),
                     json.dumps(items, ensure_ascii=False), old["id"]))
                return old["id"]
            cur = self.db.execute(
                "INSERT INTO session(input_no,po_id,outer_po_id,id_type,supplier,wh_name,status,po_date,"
                "owner_co_id,auth_co_id,state,started_at,items_json,round_baseline_json) VALUES(?,?,?,?,?,?,?,?,?,?,'active',?,?,?)",
                (input_no, po_id, head.get("outer_po_id", ""), id_type, head.get("supplier", ""),
                 head.get("wh_name", ""), head.get("status", ""), head.get("po_date", ""),
                 head.get("owner_co_id", ""), head.get("auth_co_id", ""), ts,
                 json.dumps(items, ensure_ascii=False), json.dumps(items, ensure_ascii=False)))
            return cur.lastrowid

    def activate_session(self, sid):
        """把某张单设为「当前收货中」。同时把上一张 active 的单自动挂起，避免两张都亮着。

        先把旧的 active 全部改成 paused 再置新的 —— 顺序不能反，
        否则中途会有两行 state='active'（虽然索引已删，但语义上就乱了）。
        """
        s = self.get_session(sid)
        if not s or s["state"] == "closed":
            raise ValueError("该轮已结束，请新建收货轮次，不能重开历史记录")
        with self.db:
            self.db.execute("UPDATE session SET state='paused', paused_at=? WHERE state='active' AND id<>?",
                            (now_str(), sid))
            self.db.execute("UPDATE session SET state='active', ended_at=NULL, paused_at=NULL WHERE id=?", (sid,))

    def pause_session(self, sid):
        """退出批次：挂起，数据全部保留"""
        with self.db:
            self.db.execute("UPDATE session SET state='paused', paused_at=? WHERE id=? AND state='active'",
                            (now_str(), sid))

    def close_session(self, sid):
        with self.db:
            self.db.execute("UPDATE session SET state='closed', ended_at=? WHERE id=? AND state<>'closed'",
                            (now_str(), sid))

    # ---------------- 子批次 ----------------
    def batches(self, sid):
        rows = self.db.execute(
            "SELECT b.id, b.tracking_no, b.first_scan_at, "
            "ib.job_id AS inbound_job_id, ij.io_id AS inbound_io_id, ij.status AS inbound_status, "
            "(SELECT COALESCE(SUM(l.qty),0) FROM line l LEFT JOIN inbound_line il ON il.line_id=l.id WHERE l.batch_id=b.id AND il.job_id IS NULL AND (ib.batch_id IS NULL OR ib.line_scoped=1)) AS inbound_pending_qty, "
            "  (SELECT COUNT(*) FROM line l WHERE l.batch_id=b.id) AS kinds, "
            "  (SELECT COALESCE(SUM(l.qty),0) FROM line l WHERE l.batch_id=b.id) AS total "
            "FROM batch b LEFT JOIN inbound_batch ib ON ib.batch_id=b.id "
            "LEFT JOIN inbound_job ij ON ij.id=ib.job_id WHERE b.session_id=? ORDER BY b.id", (sid,)).fetchall()
        return [dict(r) for r in rows]

    def ensure_batch(self, sid, tracking_no):
        tracking_no = (tracking_no or "").strip()
        if not tracking_no:
            raise ValueError("物流单号不能为空")
        r = self.db.execute("SELECT * FROM batch WHERE session_id=? AND tracking_no=?", (sid, tracking_no)).fetchone()
        if r:
            return dict(r), False
        ts = now_str()
        with self.db:
            cur = self.db.execute("INSERT INTO batch(session_id,tracking_no,first_scan_at) VALUES(?,?,?)",
                                  (sid, tracking_no, ts))
        row = self.db.execute("SELECT * FROM batch WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row), True

    def batch_lines(self, batch_id, order=None):
        """当前物流单号下的明细。

        排序规则（前端可直接用）：
          1. matched=1（采购单里有的）排前面，matched=0（采购单没有的）沉底；
          2. 采购单里有的，按「采购单原始顺序」排 —— order 传 {poi_id: 序号} 时用它，
             没有 order 就退回按入库顺序（id）；
          3. 采购单没有的，按入库顺序排在最后。
        """
        rows = self.db.execute(
            "SELECT l.*, ib.job_id AS inbound_job_id, "
            "CASE WHEN ib.line_scoped=1 THEN il.job_id ELSE ib.job_id END AS submitted_job_id FROM line l "
            "LEFT JOIN inbound_batch ib ON ib.batch_id=l.batch_id "
            "LEFT JOIN inbound_line il ON il.line_id=l.id WHERE l.batch_id=? ORDER BY l.id", (batch_id,)).fetchall()
        out = [dict(r) for r in rows]
        if order:
            def rank(l):
                if not l.get("matched"):
                    return (2, 0, l["id"])
                seq = order.get(str(l.get("poi_id") or ""), 10 ** 6)
                return (1, seq, l["id"])
        else:
            def rank(l):
                return (0 if l.get("matched") else 1, 0, l["id"])
        out.sort(key=rank)
        return out

    def batches_with_lines(self, sid, order=None):
        """记录查询用：按物流单号分组，每组带上自己的明细行。

        同一物流单号内同编码会累加成一行（add_qty 按 UNIQUE(batch_id,key) 累加），
        不同物流单号是各自独立的行，互不影响。
        order 传 {poi_id: 序号} 时，组内明细按采购单顺序排（采购单没有的沉底）。
        """
        out = []
        for b in self.batches(sid):
            b = dict(b)
            b["lines"] = self.batch_lines(b["id"], order=order)
            out.append(b)
        return out

    # ---------------- 商品资料缓存 ----------------
    def sku_cache_get(self, code, ttl_days=7):
        code = (code or "").strip()
        if not code:
            return None
        r = self.db.execute("SELECT * FROM sku_cache WHERE code=?", (code,)).fetchone()
        if not r:
            return None
        if ttl_days > 0:
            try:
                age = (datetime.now() - datetime.strptime(r["updated_at"], "%Y-%m-%d %H:%M:%S")).days
                if age > ttl_days:
                    return None
            except Exception:
                return None
        return dict(r)

    def sku_cache_put(self, code, info, owner_co_id=""):
        """info 为 None 表示「确认查无此商品」的负缓存"""
        if not (code or "").strip():
            return
        if not info:
            with self.db:
                self.db.execute(
                    "INSERT INTO sku_cache(code,hit,owner_co_id,updated_at) VALUES(?,0,?,?) "
                    "ON CONFLICT(code) DO UPDATE SET hit=0, owner_co_id=excluded.owner_co_id, updated_at=excluded.updated_at",
                    (code, str(owner_co_id or ""), now_str()))
            return
        with self.db:
            self.db.execute(
                "INSERT INTO sku_cache(code,hit,sku_id,i_id,name,spec,sku_code,enabled,pic,owner_co_id,updated_at)"
                " VALUES(?,1,?,?,?,?,?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET "
                "hit=1,sku_id=excluded.sku_id,i_id=excluded.i_id,name=excluded.name,spec=excluded.spec,"
                "sku_code=excluded.sku_code,enabled=excluded.enabled,pic=excluded.pic,"
                "owner_co_id=excluded.owner_co_id,updated_at=excluded.updated_at",
                (code, info.get("sku_id", ""), info.get("i_id", ""), info.get("name", ""),
                 info.get("spec", ""), info.get("sku_code", ""), info.get("enabled", ""),
                 info.get("pic", ""), str(owner_co_id or ""), now_str()))

    def sku_cache_clear(self):
        with self.db:
            n = self.db.execute("SELECT COUNT(*) c FROM sku_cache").fetchone()["c"]
            self.db.execute("DELETE FROM sku_cache")
        return n

    def sku_cache_count(self):
        return self.db.execute("SELECT COUNT(*) c FROM sku_cache").fetchone()["c"]

    # ---------------- 删除 ----------------
    def del_line(self, line_id):
        with self.db:
            self.db.execute("DELETE FROM line WHERE id=?", (line_id,))

    def del_batch(self, batch_id):
        """删除子批次及其全部明细行"""
        with self.db:
            n = self.db.execute("SELECT COUNT(*) c FROM line WHERE batch_id=?", (batch_id,)).fetchone()["c"]
            self.db.execute("DELETE FROM line WHERE batch_id=?", (batch_id,))
            self.db.execute("DELETE FROM batch WHERE id=?", (batch_id,))
        return n

    def del_session(self, sid):
        """删除整张采购单收货记录（含子批次与明细）"""
        if self.inbound_jobs(sid):
            raise ValueError("该收货批次存在入库记录，不可删除")
        b = self.db.execute("SELECT COUNT(*) c FROM batch WHERE session_id=?", (sid,)).fetchone()["c"]
        n = self.db.execute("SELECT COUNT(*) c FROM line WHERE session_id=?", (sid,)).fetchone()["c"]
        with self.db:
            self.db.execute("DELETE FROM line WHERE session_id=?", (sid,))
            self.db.execute("DELETE FROM batch WHERE session_id=?", (sid,))
            self.db.execute("DELETE FROM session WHERE id=?", (sid,))
        return {"batches": b, "lines": n}

    def rename_batch(self, batch_id, tracking_no):
        """编辑物流单号：明细保持不变，只改单号本身。同一收货单内不允许重名。"""
        tracking_no = (tracking_no or "").strip()
        if not tracking_no:
            raise ValueError("物流单号不能为空")
        r = self.db.execute("SELECT * FROM batch WHERE id=?", (batch_id,)).fetchone()
        if not r:
            raise ValueError("找不到该物流单号")
        dup = self.db.execute(
            "SELECT id FROM batch WHERE session_id=? AND tracking_no=? AND id<>?",
            (r["session_id"], tracking_no, batch_id)).fetchone()
        if dup:
            raise ValueError("该收货批次下已有同名物流单号")
        with self.db:
            self.db.execute("UPDATE batch SET tracking_no=? WHERE id=?", (tracking_no, batch_id))

    # ---------------- 明细行 ----------------
    # ---------------- 客户端操作去重 ----------------
    def client_op_get(self, op_id):
        if not op_id:
            return None
        r = self.db.execute("SELECT * FROM client_op WHERE id=?", (op_id,)).fetchone()
        return dict(r) if r else None

    def _client_op_insert(self, op, line_id, qty, added):
        # Same transaction as the quantity write: either both land or neither does.
        self.db.execute("INSERT INTO client_op(id,path,digest,line_id,qty,added,created_at) VALUES(?,?,?,?,?,?,?)",
                        (op["id"], op["path"], op["digest"], line_id, qty, added, now_str()))

    def client_op_finish(self, op_id, response):
        if not op_id:
            return
        with self.db:
            self.db.execute("UPDATE client_op SET response_json=? WHERE id=?",
                            (json.dumps(response, ensure_ascii=False), op_id))

    def add_qty(self, batch_id, sid, payload, step, op=None):
        """累加实收（同一物流子批次同一商品重复扫码继续累加）。

        op = {"id","path","digest"}：可选的客户端操作编号，与数量写入同一事务落库，
        同一编号的重复请求由调用方先查 client_op_get 后直接回放，不再累加。"""
        now = now_str()
        key = "sku:" + str(payload.get("key") or payload.get("sku_id"))
        with self.db:
            r = self.db.execute("SELECT * FROM line WHERE batch_id=? AND key=?", (batch_id, key)).fetchone()
            if r:
                q = float(r["qty"]) + step
                if q <= 0:
                    q = 0
                self.db.execute("UPDATE line SET qty=?, updated_at=? WHERE id=?", (q, now, r["id"]))
                lid = r["id"]
            else:
                cur = self.db.execute(
                    "INSERT INTO line(session_id,batch_id,poi_id,key,sku_id,i_id,sku_code,name,spec,qty,matched,updated_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (sid, batch_id, payload.get("poi_id", ""), key, payload.get("sku_id", ""),
                     payload.get("i_id", ""), payload.get("sku_code", ""), payload.get("name", ""),
                     payload.get("spec", ""), step, 1 if payload.get("matched", True) else 0, now))
                lid, q = cur.lastrowid, step
            if op:
                self._client_op_insert(op, lid, q, step)
        return lid, q

    def edit_receipt_line(self, line_id, sid, batch_id, values, expected):
        """Local receipt metadata only. Never modifies ERP or the purchase baseline."""
        fields = {"sku_id": 200, "name": 300, "sku_code": 200, "spec": 300}
        if not isinstance(values, dict):
            raise ValueError("商品资料格式无效")
        clean = {}
        for field, limit in fields.items():
            value = values.get(field)
            if not isinstance(value, str) or len(value.strip()) > limit or any(ord(c) < 32 for c in value):
                raise ValueError("商品资料字段无效或过长")
            clean[field] = value.strip()
        if not clean["sku_id"] or not clean["name"]:
            raise ValueError("商品编码和名称不能为空")
        with self.db:
            row = self.db.execute("SELECT * FROM line WHERE id=? AND session_id=? AND batch_id=?",
                                  (line_id, sid, batch_id)).fetchone()
            if not row:
                raise ValueError("明细与收货批次不匹配，请刷新后重试")
            if self.db.execute("SELECT 1 FROM inbound_batch WHERE batch_id=?", (batch_id,)).fetchone():
                raise ValueError("已关联入库单的实收明细不可修改，请继续原单")
            if row["matched"]:
                raise ValueError("采购单内商品不可手动更换资料；仅可编辑非采购单商品")
            checks = ("sku_id", "name", "sku_code", "spec", "qty", "key", "matched")
            if not isinstance(expected, dict) or any(expected.get(k) != row[k] for k in checks):
                raise ValueError("明细已发生变化（可能有其他终端收货），请刷新后重新编辑")
            session = self.get_session(sid)
            base = json.loads(session["items_json"] or "[]")
            hits = [p for p in base if str(p.get("sku_id") or "").strip() == clean["sku_id"]]
            if len(hits) > 1:
                raise ValueError("该编码在采购单有多行，不能自动确定采购明细")
            purchase = hits[0] if hits else {}
            key = "sku:" + ("" if hits else "RAW:") + clean["sku_id"]
            if self.db.execute("SELECT 1 FROM line WHERE batch_id=? AND sku_id=? AND id<>?",
                               (batch_id, clean["sku_id"], line_id)).fetchone():
                raise ValueError("当前物流子批次已有该编码，请先核对两行数量；不会自动合并或覆盖")
            self.db.execute("UPDATE line SET key=?,sku_id=?,name=?,sku_code=?,spec=?,matched=?,poi_id=?,i_id=?,updated_at=? WHERE id=?",
                (key, clean["sku_id"], clean["name"], clean["sku_code"], clean["spec"], int(bool(hits)),
                 purchase.get("poi_id", ""), purchase.get("i_id", ""), now_str(), line_id))
        return dict(self.db.execute("SELECT * FROM line WHERE id=?", (line_id,)).fetchone())

    def set_qty(self, line_id, qty, op=None):
        qty = validate_qty(qty, allow_zero=True)
        with self.db:
            self.db.execute("UPDATE line SET qty=?, updated_at=? WHERE id=?", (qty, now_str(), line_id))
            if op:
                self._client_op_insert(op, line_id, qty, None)
        return qty

    # ---------------- 汇总 / 差异 ----------------
    def received_summary(self, sid):
        """按 key 汇总全采购单各批次实收数量"""
        rows = self.db.execute(
            "SELECT key, MAX(sku_id) sku_id, MAX(name) name, MAX(sku_code) sku_code, "
            "MAX(spec) spec, MAX(poi_id) poi_id, SUM(qty) qty, MIN(matched) matched "
            "FROM line WHERE session_id=? GROUP BY key", (sid,)).fetchall()
        return [dict(r) for r in rows]

    def purchase_balance(self, sid):
        """ERP remainder and local receipts are separate ledgers; never subtract both.

        A linked batch can still be a pending ERP draft, NOT confirmed inventory.
        Missing counters remain unknown, and repeated SKU rows aggregate once.
        """
        from decimal import Decimal, InvalidOperation
        def number(value):
            try:
                n = Decimal(str(value))
                return n if n.is_finite() and n >= 0 else None
            except (InvalidOperation, ValueError, TypeError):
                return None
        s = self.get_session(sid)
        rows = {}
        for item in json.loads((s or {}).get("items_json") or "[]"):
            sku = str(item.get("sku_id") or "")
            row = rows.setdefault(sku, {"sku_id": sku, "name": item.get("name") or "",
                "ordered": Decimal(0), "erp_received": Decimal(0),
                "local_pending": Decimal(0), "local_linked": Decimal(0)})
            for key, field in (("ordered", "qty"), ("erp_received", "in_qty")):
                value = number(item.get(field)) if item.get(field + "_known", False) else None
                row[key] = row[key] + value if row[key] is not None and value is not None else None
        local = self.db.execute(
            "SELECT l.sku_id, CASE WHEN ib.line_scoped=1 THEN il.job_id ELSE ib.job_id END AS inbound_job_id, SUM(l.qty) qty FROM line l "
            "LEFT JOIN inbound_batch ib ON ib.batch_id=l.batch_id LEFT JOIN inbound_line il ON il.line_id=l.id "
            "WHERE l.session_id=? GROUP BY l.sku_id,CASE WHEN ib.line_scoped=1 THEN il.job_id ELSE ib.job_id END", (sid,))
        for item in local:
            row = rows.get(str(item["sku_id"] or ""))
            if row is not None:
                row["local_linked" if item["inbound_job_id"] else "local_pending"] += number(item["qty"]) or Decimal(0)
        for row in rows.values():
            row["remaining"] = (max(row["ordered"] - row["erp_received"], Decimal(0))
                if row["ordered"] is not None and row["erp_received"] is not None else None)
            for key, value in list(row.items()):
                if isinstance(value, Decimal): row[key] = float(value)
        return list(rows.values())

    def receiving_round(self, sid):
        from receiving_round import view
        return view(self, sid)

    def _base_items(self, sid):
        s = self.get_session(sid)
        if not s:
            return None
        try:
            return json.loads(s["items_json"] or "[]")
        except Exception:
            return []

    def sku_comparison(self, sid, base=None):
        """采购单按 SKU 合并后的应收 / 实收对照（diff_report、po_received_summary、
        左栏 po_items、"已在别的单号收满" 行共用，同一 SKU 多行时数字一致）。

        返回 (planned, extras)：planned 为 {key: 行}，含 plan/recv/diff/done/over；
        extras 为采购单里没有、但实收出现了的 received_summary 行。"""
        if base is None:
            base = self._base_items(sid) or []
        planned = plan_by_key(base)
        recv = {r["key"]: r for r in self.received_summary(sid)}
        for key, row in planned.items():
            got = recv.pop(key, None)
            r = float(got["qty"] or 0) if got else 0.0
            row.update(recv=r, diff=r - row["plan"], done=abs(r - row["plan"]) < 1e-6,
                       over=r > row["plan"] + 1e-6)
        return planned, list(recv.values())

    def po_received_summary(self, sid):
        """采购单维度逐商品实收汇总：每个采购单商品的应收 / 实收（全单合计）/ 差异。

        与差异表的区别：这里**不管是否一致都列出来**，是采购单该收什么的完整对照，
        供记录页「查看明细与差异」上方显示；差异表只列不一致的行。
        同一 SKU 在采购单出现多行时按 SKU 合并成一行（应收为各行之和）。
        """
        base = self._base_items(sid)
        if base is None:
            return []
        planned, extras = self.sku_comparison(sid, base)
        rows = []
        for p in planned.values():
            rows.append({
                "sku_id": p["sku_id"], "sku_code": p["sku_code"],
                "name": p["name"], "spec": p["spec"],
                "plan": p["plan"], "recv": p["recv"], "diff": p["diff"],
                "done": p["done"], "over": p["over"],
            })
        # 采购单里没有、但实收出现了的（计划外）也一并列出，避免漏看
        for v in extras:
            rows.append({
                "sku_id": v.get("sku_id"), "sku_code": v.get("sku_code"),
                "name": v.get("name"), "spec": v.get("spec"),
                "plan": 0, "recv": float(v.get("qty") or 0),
                "diff": float(v.get("qty") or 0),
                "done": False, "over": True, "extra": True,
            })
        return rows

    def received_by_sku_per_batch(self, sid):
        """按「物流单号 → 商品」两级展开实收，供记录页第一段（分单号明细）使用。"""
        rows = self.db.execute(
            "SELECT b.id AS batch_id, b.tracking_no, b.first_scan_at, "
            "       l.sku_id, l.sku_code, l.name, l.spec, l.qty, l.matched, l.updated_at "
            "FROM line l JOIN batch b ON b.id=l.batch_id "
            "WHERE l.session_id=? ORDER BY b.id, l.id", (sid,)).fetchall()
        return [dict(r) for r in rows]

    def batch_summary(self, sid):
        """每个物流单号的合计（种数 / 件数），供记录页分单号表格标题行显示。"""
        return self.batches(sid)

    def diff_report(self, sid):
        base = self._base_items(sid)
        if base is None:
            raise ValueError("采购单不存在")
        planned, extras = self.sku_comparison(sid, base)
        rows = []
        for p in planned.values():
            if p["done"]:
                continue                       # 一致的行不进差异表
            r, plan = p["recv"], p["plan"]
            rows.append({
                "sku_id": p["sku_id"], "sku_code": p["sku_code"],
                "name": p["name"], "spec": p["spec"],
                "plan": plan, "recv": r, "diff": r - plan,
                "reason": ("多收 %g" % (r - plan)) if r > plan else ("少收 %g" % (plan - r)),
                "kind": "diff",
            })
        for v in extras:
            rows.append({
                "sku_id": v.get("sku_id"), "sku_code": v.get("sku_code"),
                "name": v.get("name"), "spec": v.get("spec"),
                "plan": 0, "recv": float(v.get("qty") or 0), "diff": float(v.get("qty") or 0),
                "reason": "采购单无此商品" if not v.get("matched") else "采购单无此商品",
                "kind": "extra",
            })
        return rows

    def progress(self, sid):
        s = self.get_session(sid)
        try:
            base = json.loads(s["items_json"] or "[]")
        except Exception:
            base = []
        plan = sum(float(it.get("qty") or 0) for it in base)
        got = self.db.execute("SELECT COALESCE(SUM(qty),0) t FROM line WHERE session_id=? AND matched=1",
                              (sid,)).fetchone()["t"]
        return {"plan": plan, "recv": float(got or 0)}

    # ---------------- 查询 ----------------
    def find_sessions(self, no, limit=30):
        """按采购单号 / 外部单号 / 供应商 模糊查询"""
        kw = _like(no)
        rows = self.db.execute(
            "SELECT * FROM session WHERE po_id LIKE ? ESCAPE '\\' OR outer_po_id LIKE ? ESCAPE '\\' "
            "OR input_no LIKE ? ESCAPE '\\' OR IFNULL(supplier,'') LIKE ? ESCAPE '\\' "
            "ORDER BY id DESC LIMIT ?",
            (kw, kw, kw, kw, limit)).fetchall()
        return [dict(r) for r in rows]

    def find_batches(self, tracking, limit=50):
        """按物流单号模糊查询"""
        rows = self.db.execute(
            "SELECT b.*, s.po_id, s.input_no, s.supplier, s.state, s.started_at AS s_start, s.ended_at AS s_end, "
            "s.outer_po_id FROM batch b JOIN session s ON s.id=b.session_id "
            "WHERE b.tracking_no LIKE ? ESCAPE '\\' ORDER BY b.id DESC LIMIT ?",
            (_like(tracking), limit)).fetchall()
        return [dict(r) for r in rows]

    def recent_sessions(self, limit=50):
        """最近收货单。未结单的（active/paused）永远排在最前，方便一键回到挂起批次。"""
        rows = self.db.execute(
            "SELECT * FROM session ORDER BY (state='closed'), (state='paused'), id DESC LIMIT ?",
            (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ---------------- 导出 ----------------
    def export_rows(self, mode, value="", start="", end=""):
        """返回严格 6 列的行数据。"""
        sql = ("SELECT s.po_id, b.tracking_no, b.first_scan_at, l.sku_id, l.name, l.qty "
               "FROM line l JOIN batch b ON b.id=l.batch_id JOIN session s ON s.id=l.session_id ")
        if mode == "po":
            sql += "WHERE s.po_id=? OR s.outer_po_id=? OR s.input_no=? ORDER BY b.id, l.id"
            args = (value, value, value)
        elif mode == "tracking":
            sql += "WHERE b.tracking_no LIKE ? ORDER BY b.id, l.id"
            args = ("%" + value + "%",)
        elif mode == "range":
            sql += "WHERE b.first_scan_at>=? AND b.first_scan_at<=? ORDER BY b.id, l.id"
            args = (start + " 00:00:00", end + " 23:59:59")
        else:
            sql += "ORDER BY b.id DESC, l.id"
            args = ()
        rows = self.db.execute(sql, args).fetchall()
        out = []
        for r in rows:
            out.append([r["po_id"], r["tracking_no"], r["first_scan_at"],
                        r["sku_id"], r["name"], _fmt_num(r["qty"])])
        return out


def _like(s):
    """模糊匹配关键字：转义 \\ % _ 三个 LIKE 通配符，避免用户输入被当成通配"""
    s = "" if s is None else str(s).strip()
    s = s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return "%" + s + "%"


def _fmt_num(v):
    f = float(v or 0)
    return str(int(f)) if abs(f - int(f)) < 1e-6 else ("%g" % f)
