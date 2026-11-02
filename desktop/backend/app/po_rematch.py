# -*- coding: utf-8 -*-
"""有采购单收货：更换采购单号 / 外部单号并重新匹配已收的商品。

两步走：preview() 只读，算出「新采购单上有 / 新采购单上没有 / 超收」并给出一个确认指纹；
apply() 在确认时由调用方重新查一次聚水潭、重新 preview，指纹一致才写库（不一致 E-PO-2005）。

不变的：收货批次编号、物流单号（batch）、每行实收数量、扫码记录、录像关联（都按批次挂在同一个
收货批次上，跟着换到新单号）。会变的：收货批次的采购单号 / 外部单号 / 货主仓库信息、本轮应收基线，
以及每行明细是否在采购单上（key / matched / poi_id）。

拦截：本轮已有入库记录（任何状态）E-PO-2002；已结单 E-PO-2007；新旧相同 E-PO-2001；
新单已有未结束批次 E-PO-2003；跨货主 / 仓库 E-PO-2004；新单在其他收货方式已有入库记录 E-PO-2006。
数据库触发器 inbound_session_context 另外兜底：有入库记录的批次改不了采购单号，杜绝重复入库。
"""
import hashlib
import json
from decimal import Decimal

from errors import AppError
from receiving_round import round_targets

SCHEMA = """
CREATE TABLE IF NOT EXISTS po_rematch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sid INTEGER NOT NULL,
    at TEXT NOT NULL,
    old_po TEXT, old_outer TEXT, old_input TEXT,
    new_po TEXT, new_outer TEXT, new_input TEXT,
    detail_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_po_rematch_sid ON po_rematch_log(sid);
"""


def ensure_schema(store):
    store.db.executescript(SCHEMA)


def _num(v):
    try:
        return Decimal(str(v or 0))
    except Exception:
        return Decimal(0)


def _fmt(d):
    d = Decimal(d)
    return int(d) if d == d.to_integral_value() else float(d)


def check_session(store, sid):
    s = store.get_session(sid) if sid else None
    if not s:
        raise AppError("E-PO-1000", "找不到该收货批次")
    if str(s.get("id_type") or "") in ("unplanned", "express", "joint-allocation"):
        raise AppError("E-PO-1000", "只有「有采购单收货」的批次可以更换采购单")
    if s.get("state") == "closed":
        raise AppError("E-PO-2007")
    jobs = store.db.execute("SELECT COUNT(*) FROM inbound_job WHERE session_id=?", (sid,)).fetchone()[0]
    if jobs:
        raise AppError("E-PO-2002", "本轮已生成过 %d 张入库单（含失败 / 待核实），不能更换采购单" % jobs)
    return s


def _lines(store, sid):
    return [dict(r) for r in store.db.execute(
        "SELECT l.id,l.batch_id,l.key,l.sku_id,l.sku_code,l.name,l.spec,l.qty,l.matched,l.poi_id,b.tracking_no"
        " FROM line l JOIN batch b ON b.id=l.batch_id WHERE l.session_id=? ORDER BY l.batch_id,l.id", (sid,))]


def _index(items):
    by_sku, by_code = {}, {}
    for it in items:
        sku = str(it.get("sku_id") or "")
        if sku and sku not in by_sku:
            by_sku[sku] = it
        for code in [str(it.get("sku_code") or "")] + [c for c in str(it.get("aux_codes") or "").split(",")]:
            code = code.strip()
            if code and code not in by_code:
                by_code[code] = it
    return by_sku, by_code


def _target_item(line, by_sku, by_code):
    sku = str(line.get("sku_id") or "")
    if sku in by_sku:
        return by_sku[sku]
    code = str(line.get("sku_code") or "")
    if not line.get("matched"):
        # 没在旧单上的、只认出条码的商品：按条码 / 辅助码再找一次
        for c in (code, sku):
            if c and c in by_code:
                return by_code[c]
    return None


def preview(store, sid, row, ctx, items, other_inbound=None):
    """只读：新采购单对已收商品的匹配结果。row/ctx/items 来自 find_purchase_order/fetch_items。"""
    s = check_session(store, sid)
    new_po = str(row.get("po_id") or "")
    owner, auth = str(ctx.get("owner") or ""), str(ctx.get("auth") or "")
    old_owner, old_auth = str(s.get("owner_co_id") or ""), str(s.get("auth_co_id") or "")
    if (old_owner and owner and old_owner != owner) or (old_auth and auth and old_auth != auth):
        raise AppError("E-PO-2004", "新采购单 %s 属于其他货主或仓库公司，不能直接更换" % new_po)
    if new_po == str(s.get("po_id") or ""):
        raise AppError("E-PO-2001", "新旧采购单相同（%s），不需要更换" % new_po)
    other = store.find_session_by_po(new_po, owner or old_owner, auth or old_auth)
    if other and other["state"] != "closed" and other["id"] != sid:
        raise AppError("E-PO-2003", "采购单 %s 已有未结束的收货批次，请在挂起批次里打开它继续收货" % new_po)
    if other_inbound:
        raise AppError("E-PO-2006", "采购单 %s 在%s已有入库记录，不能换过来重复入库" % (new_po, other_inbound))

    by_sku, by_code = _index(items)
    lines = _lines(store, sid)
    changes, received = [], {}
    for ln in lines:
        it = _target_item(ln, by_sku, by_code)
        if it:
            sku = str(it.get("sku_id") or "")
            new_key, matched, poi = "sku:" + sku, 1, str(it.get("poi_id") or "")
            new_sku = sku
        else:
            sku = str(ln.get("sku_id") or ln.get("sku_code") or "")
            new_key, matched, poi, new_sku = "sku:RAW:" + sku, 0, "", str(ln.get("sku_id") or "")
        changes.append({"line_id": ln["id"], "batch_id": ln["batch_id"], "tracking": ln["tracking_no"],
                        "old_key": ln["key"], "new_key": new_key, "sku_id": new_sku, "matched": matched,
                        "poi_id": poi, "qty": ln["qty"], "name": (it or {}).get("name") or ln.get("name") or "",
                        "spec": (it or {}).get("spec") or ln.get("spec") or "",
                        "sku_code": (it or {}).get("sku_code") or ln.get("sku_code") or "",
                        "was_matched": bool(ln.get("matched"))})
        bucket = received.setdefault((matched, new_sku if matched else sku), {
            "sku_id": new_sku if matched else (ln.get("sku_id") or ""), "sku_code": (it or ln).get("sku_code") or "",
            "name": (it or {}).get("name") or ln.get("name") or "", "spec": (it or {}).get("spec") or ln.get("spec") or "",
            "received": Decimal(0), "was_matched": bool(ln.get("matched"))})
        bucket["received"] += _num(ln["qty"])

    targets = round_targets({"round_baseline_json": json.dumps(items, ensure_ascii=False)})
    ordered = {}
    for it in items:
        sku = str(it.get("sku_id") or "")
        ordered[sku] = ordered.get(sku, Decimal(0)) + _num(it.get("qty"))
    matches, missing, over = [], [], []
    for (matched, _), b in received.items():
        if b["received"] <= 0 and not matched:
            continue
        if matched:
            plan = targets.get(b["sku_id"])
            basis = "round" if plan is not None else "total"
            plan = plan if plan is not None else ordered.get(b["sku_id"], Decimal(0))
            row_out = {"sku_id": b["sku_id"], "sku_code": b["sku_code"], "name": b["name"], "spec": b["spec"],
                       "received": _fmt(b["received"]), "plan": _fmt(plan), "plan_basis": basis,
                       "newly_matched": not b["was_matched"]}
            matches.append(row_out)
            if b["received"] > plan:
                over.append(dict(row_out, over=_fmt(b["received"] - plan)))
        else:
            missing.append({"sku_id": b["sku_id"], "sku_code": b["sku_code"], "name": b["name"], "spec": b["spec"],
                            "received": _fmt(b["received"])})
    got = {m["sku_id"] for m in matches}
    not_received = [{"sku_id": str(it.get("sku_id") or ""), "name": it.get("name") or "", "spec": it.get("spec") or "",
                     "qty": it.get("qty")} for it in items if str(it.get("sku_id") or "") not in got]
    head = {k: row.get(k, "") for k in ("po_id", "outer_po_id", "supplier", "wh_name", "status", "po_date")}
    head.update(owner_co_id=owner or old_owner, auth_co_id=auth or old_auth)
    batches = [dict(r) for r in store.db.execute("SELECT id,tracking_no FROM batch WHERE session_id=? ORDER BY id", (sid,))]
    basis = {"sid": sid, "old": [s.get("po_id"), s.get("outer_po_id"), s.get("state")], "head": head,
             "items": [{k: it.get(k) for k in ("poi_id", "sku_id", "sku_code", "qty", "in_qty")} for it in items],
             "lines": [[ln["id"], ln["batch_id"], ln["key"], ln["sku_id"], str(ln["qty"])] for ln in lines],
             "batches": [[b["id"], b["tracking_no"]] for b in batches]}
    fingerprint = hashlib.sha256(json.dumps(basis, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()
    return {"sid": sid, "fingerprint": fingerprint,
            "old": {"po_id": s.get("po_id") or "", "outer_po_id": s.get("outer_po_id") or "", "input_no": s.get("input_no") or ""},
            "new": dict(head, id_type=ctx.get("id_type") or ""),
            "matches": matches, "missing": missing, "over": over, "not_received": not_received,
            "batches": len(batches), "lines": len(lines),
            "total_received": _fmt(sum((_num(ln["qty"]) for ln in lines), Decimal(0))),
            "_changes": changes, "_items": items}


def public(p):
    return {k: v for k, v in p.items() if not k.startswith("_")}


def apply(store, p, input_no, now):
    """写库（一个事务）。调用方已重新 preview 并核对指纹。返回审计记录。"""
    ensure_schema(store)
    sid = p["sid"]
    check_session(store, sid)
    head, items = p["new"], p["_items"]
    items_json = json.dumps(items, ensure_ascii=False)
    from purchase_snapshot import capture
    with store.db:
        capture(store, dict(head), items_json, now, "first_query")
        # 同一物流单里可能出现两行落到同一个 key（例如旧单外的商品正好在新单上）：数量合并到一行
        seen = {}
        for c in p["_changes"]:
            k = (c["batch_id"], c["new_key"])
            if k in seen:
                keep = seen[k]
                store.db.execute("UPDATE line SET qty=qty+(SELECT qty FROM line WHERE id=?), updated_at=? WHERE id=?",
                                 (c["line_id"], now, keep))
                store.db.execute("DELETE FROM line WHERE id=?", (c["line_id"],))
                continue
            seen[k] = c["line_id"]
        # 先把要改 key 的行挪到临时 key，避免 UNIQUE(batch_id,key) 在交换时冲突
        for c in p["_changes"]:
            if seen.get((c["batch_id"], c["new_key"])) == c["line_id"] and c["old_key"] != c["new_key"]:
                store.db.execute("UPDATE line SET key=? WHERE id=?", ("tmp:rematch:%d" % c["line_id"], c["line_id"]))
        for c in p["_changes"]:
            if seen.get((c["batch_id"], c["new_key"])) != c["line_id"]:
                continue
            store.db.execute("UPDATE line SET key=?,matched=?,poi_id=?,sku_id=?,name=?,spec=?,sku_code=? WHERE id=?",
                             (c["new_key"], c["matched"], c["poi_id"], c["sku_id"] or "", c["name"], c["spec"],
                              c["sku_code"], c["line_id"]))
        store.db.execute(
            "UPDATE session SET input_no=?,po_id=?,outer_po_id=?,id_type=?,supplier=?,wh_name=?,status=?,po_date=?,"
            "owner_co_id=?,auth_co_id=?,items_json=?,round_baseline_json=? WHERE id=?",
            (input_no, head["po_id"], head.get("outer_po_id", ""), head.get("id_type") or "po_id", head.get("supplier", ""),
             head.get("wh_name", ""), head.get("status", ""), head.get("po_date", ""), head.get("owner_co_id", ""),
             head.get("auth_co_id", ""), items_json, items_json, sid))
        store.db.execute("INSERT OR REPLACE INTO kv(k,v) VALUES(?,?)", ("po_refreshed_at:" + str(sid), now))
        detail = {"matches": len(p["matches"]), "missing": p["missing"], "over": p["over"],
                  "batches": p["batches"], "lines": p["lines"], "total_received": p["total_received"],
                  "changed_lines": [{k: c[k] for k in ("line_id", "tracking", "old_key", "new_key", "qty")}
                                    for c in p["_changes"] if c["old_key"] != c["new_key"]]}
        store.db.execute(
            "INSERT INTO po_rematch_log(sid,at,old_po,old_outer,old_input,new_po,new_outer,new_input,detail_json)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (sid, now, p["old"]["po_id"], p["old"]["outer_po_id"], p["old"]["input_no"], head["po_id"],
             head.get("outer_po_id", ""), input_no, json.dumps(detail, ensure_ascii=False, default=str)))
    return detail


def history(store, sid):
    ensure_schema(store)
    return [dict(r, detail=json.loads(r["detail_json"] or "{}")) for r in store.db.execute(
        "SELECT * FROM po_rematch_log WHERE sid=? ORDER BY id", (sid,))]
