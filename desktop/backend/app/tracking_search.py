"""按物流单号查登记记录：四种收货来源共用的只读检索。

每种收货方式的数据在各自的库里：采购单收货 jst.db、无采购单收货 unplanned.db、
联合收货 joint_receipts.db、快递单登记 express.db。记录查询、录像查询（电脑端和
远程管理页）都按这里的规则在每个库里找物流单号，任何一个库都不会被漏掉。

只做 SELECT，不改任何数据；库里缺表（旧版本或没用过的功能）时直接返回空。
"""

SOURCE_LABELS = {
    "普通": "采购单收货",
    "无采购单": "无采购单收货",
    "联合": "联合收货",
    "快递单": "快递单登记",
}
# 远程管理页的类型代码 → 录像里的收货来源
TYPE_SOURCES = {"po": "普通", "unplanned": "无采购单", "joint": "联合", "express": "快递单"}


def like(text):
    """模糊匹配关键字：去掉首尾空白，转义 \\ % _，避免用户输入被当成通配符"""
    s = "" if text is None else str(text).strip()
    s = s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return "%" + s + "%"


def _tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


def _dicts(cur):
    names = [d[0] for d in cur.description]
    return [dict(zip(names, tuple(r))) for r in cur.fetchall()]


def hits(db, source, query, limit=50):
    """在一个库里按物流单号（包含即可，不分大小写）找登记过的物流批次。

    source 是录像用的收货来源：普通 / 无采购单 / 联合 / 快递单。
    返回 [{source, label, sid, bid, tracking_no, first_scan_at, input_no, po_id, state,
    started_at, ended_at, note, total}]，新的在前。
    """
    q = "" if query is None else str(query).strip()
    if db is None or not q or source not in SOURCE_LABELS:
        return []
    tables = _tables(db)
    if not {"session", "batch", "line"} <= tables:
        return []
    join, note, where = "", "''", ["b.tracking_no LIKE ? ESCAPE '\\'"]
    if source in ("无采购单", "快递单"):
        if "unplanned_meta" not in tables:
            return []
        join = " JOIN unplanned_meta m ON m.sid=s.id"
        note = "IFNULL(m.note,'')"
        if "joint_group" in tables:
            where.append("s.id NOT IN (SELECT sid FROM joint_group)")
    elif source == "联合":
        if "joint_group" not in tables:
            return []
        join = " JOIN joint_group g ON g.sid=s.id"
    else:
        where.append("IFNULL(s.id_type,'')<>'joint-allocation'")
    cur = db.execute(
        "SELECT b.id AS bid, s.id AS sid, b.tracking_no, b.first_scan_at, s.input_no, "
        "IFNULL(s.po_id,'') AS po_id, s.state, s.started_at, s.ended_at, " + note + " AS note, "
        "(SELECT IFNULL(SUM(l.qty),0) FROM line l WHERE l.batch_id=b.id) AS total "
        "FROM batch b JOIN session s ON s.id=b.session_id" + join + " WHERE " + " AND ".join(where) +
        " ORDER BY b.id DESC LIMIT ?", (like(q), max(1, min(int(limit or 50), 200))))
    out = []
    for r in _dicts(cur):
        r["source"] = source
        r["label"] = SOURCE_LABELS[source]
        out.append(r)
    return out


def merge(groups, limit=50):
    """合并多个库的结果，按首次扫描时间新的在前"""
    rows = [r for g in groups for r in (g or [])]
    rows.sort(key=lambda r: (str(r.get("first_scan_at") or r.get("started_at") or ""), r.get("bid") or 0), reverse=True)
    return rows[:limit]


def video_key(row):
    """与录像片段的 BatchKey（来源:会话:批次）一致，用来判断这一单有没有录像"""
    return "%s:%s:%s" % (row.get("source"), row.get("sid"), row.get("bid"))
