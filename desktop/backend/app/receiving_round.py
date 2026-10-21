# -*- coding: utf-8 -*-
"""Receiving-round view. A session is one round; tracking batches are its children.

ERP snapshots may change. The start snapshot never does. Links to drafts are NOT
proof of ERP confirmation. Legacy sessions have no start snapshot and stay unknown.
"""
import json
from decimal import Decimal, InvalidOperation


def number(value):
    try:
        n = Decimal(str(value))
        return n if n.is_finite() and n >= 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def load(value):
    try:
        rows = json.loads(value or "[]")
        return rows if isinstance(rows, list) else []
    except (ValueError, TypeError):
        return []


def aggregate(items):
    rows = {}
    for i, it in enumerate(items):
        sku = str(it.get("sku_id") or "")
        key = sku or "__missing_sku:" + str(it.get("poi_id") or i)
        row = rows.setdefault(key, dict(it, sku_id=sku, ordered=Decimal(0),
            erp_received=Decimal(0), sku_codes=[], auxiliary_codes=[], row_count=0, original_index=i))
        row["row_count"] += 1
        for field, out in (("qty", "ordered"), ("in_qty", "erp_received")):
            n = number(it.get(field)) if it.get(field + "_known") and sku else None
            row[out] = row[out] + n if row[out] is not None and n is not None else None
        for code in [str(it.get("sku_code") or "").strip()]:
            if code and code not in row["sku_codes"]: row["sku_codes"].append(code)
        from codes import item_auxiliary
        for code in item_auxiliary(it):
            if code and code not in row["auxiliary_codes"]: row["auxiliary_codes"].append(code)
    return rows


def round_targets(session):
    """本轮应收（按 SKU）：本轮起始基线的 采购数量 − ERP已入库，与 view() 的 plan 同一口径。

    只返回能可靠算出的 SKU：{sku_id: Decimal}。旧记录（没有起始基线）或缺少可靠数量的
    SKU 不在结果里，由调用方标为「本轮应收待核实」并按采购总数对照（plan_basis='total'）。
    差异表、左栏 po_items、远程页都用它，避免同一屏出现两套「应收」。
    """
    baseline = (session or {}).get("round_baseline_json")
    if baseline is None:
        return {}
    out = {}
    for key, start in aggregate(load(baseline)).items():
        if key.startswith("__missing_sku:"):
            continue
        if start["ordered"] is not None and start["erp_received"] is not None:
            out[key] = max(start["ordered"] - start["erp_received"], Decimal(0))
    return out


def view(store, sid):
    session = store.get_session(sid) or {}
    baseline = session.get("round_baseline_json")
    legacy = baseline is None
    initial = aggregate(load(baseline))
    latest = aggregate(load(session.get("items_json")))
    # Aggregate by SKU, not by historical line.key conventions.
    received = {}
    for row in store.db.execute("SELECT sku_id,sku_code,name,spec,qty FROM line WHERE session_id=? ORDER BY id", (sid,)):
        key = str(row["sku_id"] or "")
        entry = received.setdefault(key, dict(row, total=Decimal(0)))
        entry["total"] += number(row["qty"]) or Decimal(0)
    keys = list(latest)
    keys += [k for k in initial if k not in keys]
    keys += [k for k in received if k not in keys]
    rows, warnings = [], []
    def warn(message):
        if message not in warnings: warnings.append(message)
    if legacy: warn("旧记录缺少本轮起始基线，应收和差额待核实；原收货记录保留。")
    for key in keys:
        start, live, got = initial.get(key), latest.get(key), received.get(key)
        meta = live or start or got or {}
        target = None
        extra = not start and not live and bool(got)
        if not legacy:
            if start and start["ordered"] is not None and start["erp_received"] is not None:
                target = max(start["ordered"] - start["erp_received"], Decimal(0))
            elif extra:
                target = Decimal(0)
            else:
                warn("部分商品缺少可靠起始数量或为本轮开始后新增，应收和差额待核实。")
        if start and live:
            if start["ordered"] != live["ordered"]:
                warn("ERP采购数量较本轮开始发生变化，请核对；本轮目标未自动改写。")
            if start["erp_received"] != live["erp_received"]:
                warn("ERP入库数量较本轮开始发生变化，请核对相关入库单；不能仅凭余额认定本轮已确认，本轮目标保持不变。")
        elif start and not live:
            warn("本轮起始商品已不在当前ERP明细中，仍保留本轮目标和实收，请核对。")
        elif live and not start and not legacy:
            warn("ERP新增商品不在本轮起始基线内，该商品应收待核实。")
        if live and (live["ordered"] is None or live["erp_received"] is None):
            warn("ERP部分采购或入库数量未提供，显示待核实，不按零处理。")
        qty = got["total"] if got else Decimal(0)
        delta = qty - target if target is not None else None
        row = {k: meta.get(k) for k in ("sku_id", "name", "spec", "sku_code", "aux_codes", "poi_id")}
        row.update(sku_codes=meta.get("sku_codes") or ([meta["sku_code"]] if meta.get("sku_code") else []),
            auxiliary_codes=meta.get("auxiliary_codes") or [], row_count=meta.get("row_count", 1),
            original_index=live.get("original_index") if live else None,
            ordered=live["ordered"] if live else None, erp_received=live["erp_received"] if live else None,
            plan=target, recv=qty, diff=delta, extra=extra,
            done=delta is not None and abs(delta) < Decimal("0.000001"),
            over=delta is not None and delta > Decimal("0.000001"),
            reason="待核实" if delta is None else ("收满" if abs(delta)<Decimal("0.000001") else
                (("多收 %g" % delta) if delta>0 else ("还差 %g" % -delta))))
        rows.append({k: float(v) if isinstance(v, Decimal) else v for k, v in row.items()})
    known = not legacy and bool(rows) and all(r["plan"] is not None for r in rows)
    total_plan = sum((Decimal(str(r["plan"])) for r in rows), Decimal(0)) if known else None
    total_recv = sum((v["total"] for v in received.values()), Decimal(0))
    return {"id": sid, "legacy": legacy, "started_at": session.get("started_at"),
        "scope": "round", "warnings": warnings, "rows": rows,
        "progress": {"plan": float(total_plan) if total_plan is not None else None,
                     "recv": float(total_recv), "known": known},
        "diff": [r for r in rows if not r["done"]]}
