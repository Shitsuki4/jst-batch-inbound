# -*- coding: utf-8 -*-
"""把 HTTP 业务请求翻译成操作记录。

由 server.Handler._run_logged 在 state_lock 内调用：
* before()：只读快照（明细原数量、物流单号、收货单信息、设置原值、登录身份），并对入库请求先记一条「提交入库单请求」；
* after()：根据响应和 scan_audit 增量（store 触发器记录的每次明细数量变化，含操作前 / 操作后）生成记录。
两者都不改业务库、不抛异常；写入失败由 OpLog 自己吞掉。
"""
import json
import time

from oplog import is_secret_key

RECEIPT_PATHS = {
    "/api/po/create", "/api/po/activate", "/api/tracking/scan", "/api/item/scan", "/api/item/choose",
    "/api/line/edit", "/api/line/set", "/api/line/del", "/api/batch/del", "/api/batch/rename",
    "/api/batch/copy", "/api/session/pause", "/api/session/pause-report", "/api/session/resume",
    "/api/session/close", "/api/session/del", "/api/inbound/create", "/api/inbound/resume",
}
LOGIN_PATHS = {"/api/login": "账号密码", "/api/sms/login": "短信验证码",
               "/api/qr/finish": "扫码 / 官方网页", "/api/login/restore": "保持登录恢复"}
SETTINGS_KEYS = ("default_id_type", "owner_ids", "scan_step", "sound", "sku_lookup", "pick_owner",
                 "pick_auth", "catalog_auto", "catalog_online", "inbound_validate_purchase",
                 "scan_ignore_disabled", "oplog_retention_days")
REMOTE_KEYS = ("enabled", "allow_download", "port", "username")
LINE_FIELDS = ("sku_id", "sku_code", "name", "spec", "i_id", "qty")

# 只读 / 预览类动作：不记录
SKIP_ACTIONS = {"preview", "resolve", "match_search", "match_preview", "copy_preview",
                "replace_preview", "poll"}
INBOUND_ACTIONS = {"create", "resume"}


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0


def _qty(v):
    if v is None:
        return None
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def source_of(app, path):
    """(source, store)：收货类请求所属来源及其数据库。"""
    if path == "/api/unplanned/action":
        return "unplanned", app.UNPLANNED.store
    if path == "/api/joint/action":
        return "joint", app.JOINT.store
    if path in RECEIPT_PATHS:
        return "receipt", app.STORE
    return None, None


def session_info(store, sid, source=""):
    if not store or not sid:
        return {}
    try:
        r = store.db.execute("SELECT id,input_no,po_id,outer_po_id,id_type,state FROM session WHERE id=?",
                             (int(sid),)).fetchone()
        if not r:
            return {}
        info = dict(r)
        if source == "joint":
            try:
                pos = [str(x[0]) for x in store.db.execute(
                    "SELECT po_id FROM joint_purchase WHERE group_sid=? ORDER BY po_id", (int(sid),))]
                if pos:
                    info["po_id"] = ",".join(pos)
            except Exception:
                pass
        return info
    except Exception:
        return {}


def line_info(store, lid):
    try:
        r = store.db.execute(
            "SELECT l.*, b.tracking_no FROM line l LEFT JOIN batch b ON b.id=l.batch_id WHERE l.id=?",
            (int(lid),)).fetchone()
        return dict(r) if r else None
    except Exception:
        return None


def batch_info(store, bid):
    try:
        r = store.db.execute("SELECT id,session_id,tracking_no FROM batch WHERE id=?", (int(bid),)).fetchone()
        if not r:
            return None
        info = dict(r)
        agg = store.db.execute("SELECT COUNT(*), COALESCE(SUM(qty),0) FROM line WHERE batch_id=?",
                               (int(bid),)).fetchone()
        info["lines"], info["total"] = agg[0], _qty(agg[1])
        return info
    except Exception:
        return None


def audit_max(store):
    try:
        return store.db.execute("SELECT COALESCE(MAX(id),0) FROM scan_audit").fetchone()[0]
    except Exception:
        return None


def _resp_sid(resp, body):
    if isinstance(resp, dict):
        s = resp.get("session")
        if isinstance(s, dict) and s.get("id"):
            return _int(s["id"])
        b = resp.get("batch")
        if isinstance(b, dict) and b.get("session_id"):
            return _int(b["session_id"])
    return _int((body or {}).get("sid"))


# ---------------------------------------------------------------- before
def before(app, h, path, body):
    ctx = {"t": time.time()}
    body = body if isinstance(body, dict) else {}
    action = str(body.get("action") or "")
    ctx["action"] = action
    try:
        ctx["identity"] = (str(app.JST.uid or ""), str(app.JST.coid or ""), str(app.JST.uname or ""))
    except Exception:
        ctx["identity"] = ("", "", "")
    try:
        source, store = source_of(app, path)
        ctx["source"] = source
        if store is not None:
            ctx["audit_max"] = audit_max(store)
            if body.get("line_id"):
                ctx["line"] = line_info(store, body.get("line_id"))
            if body.get("batch_id") and (path in ("/api/batch/del", "/api/batch/rename")
                                         or action in ("delete_batch", "rename")):
                ctx["batch"] = batch_info(store, body.get("batch_id"))
            sid = _int(body.get("sid"))
            if not sid and path in ("/api/inbound/resume",) and body.get("job_id"):
                job = app.STORE.get_inbound(_int(body.get("job_id")))
                sid = job["session_id"] if job else 0
            if not sid and ctx.get("line"):
                sid = ctx["line"]["session_id"]
            if not sid and ctx.get("batch"):
                sid = ctx["batch"]["session_id"]
            if not sid and source == "receipt" and path.startswith("/api/session/"):
                s = app.STORE.active_session()
                sid = s["id"] if s else 0
            ctx["sid"] = sid
            ctx["session"] = session_info(store, sid, source)
        if path == "/api/settings":
            ctx["settings"] = {k: app.STORE.get_kv(k, "") for k in SETTINGS_KEYS}
        if path == "/api/remote/config":
            try:
                c = app.REMOTE.config()
                ctx["remote"] = {k: c.get(k) for k in REMOTE_KEYS}
            except Exception:
                ctx["remote"] = {}
        # 入库：请求发往 ERP 之前先记一条，进程中途崩溃也能看到「已提交请求」
        req = inbound_request(app, path, body, ctx)
        if req:
            app.OPLOG.write([req], h._op_device(), erp(app))
    except Exception as e:  # noqa: BLE001
        app.OPLOG._note_failure(e)
    return ctx


def erp(app):
    try:
        return {"co": app.JST.coid, "uid": app.JST.uid, "user": app.JST.uname}
    except Exception:
        return {}


def inbound_request(app, path, body, ctx):
    action = ctx.get("action")
    if path in ("/api/inbound/create", "/api/inbound/resume"):
        kind = "create" if path.endswith("create") else "resume"
    elif path in ("/api/unplanned/action", "/api/joint/action", "/api/purchase-watch/action") and action in INBOUND_ACTIONS:
        kind = action
    else:
        return None
    if body.get("confirmed") is not True:
        return None
    source = {"/api/unplanned/action": "unplanned", "/api/joint/action": "joint",
              "/api/purchase-watch/action": str(body.get("source") or "")}.get(path, "receipt")
    sid = _int(body.get("sid")) or ctx.get("sid") or 0
    info = ctx.get("session") or {}
    if not info and source in ("receipt", "unplanned", "joint") and sid:
        try:
            store = {"receipt": app.STORE, "unplanned": app.UNPLANNED.store, "joint": app.JOINT.store}[source]
            info = session_info(store, sid, source)
        except Exception:
            info = {}
    detail = {"wh_id": body.get("wh_id"), "mode": body.get("mode"), "job_id": body.get("job_id"),
              "io_id": body.get("io_id"), "stage_sid": body.get("stage_sid"), "watch_id": body.get("id")}
    ctx["inbound"] = {"source": source, "sid": sid, "info": info, "kind": kind}
    return {"module": "inbound", "op": "inbound_request", "result": "pending", "source": source,
            "sid": sid, "po_id": info.get("po_id"), "outer_po_id": info.get("outer_po_id"),
            "summary": ("提交生成待入库单请求" if kind == "create" else "继续核对原入库单")
                       + ("（仓库 %s）" % body.get("wh_id") if body.get("wh_id") else ""),
            "detail": {k: v for k, v in detail.items() if v not in (None, "")}}


# ---------------------------------------------------------------- after
def after(app, h, path, body, resp, ctx):
    try:
        records = build(app, path, body if isinstance(body, dict) else {}, resp if isinstance(resp, dict) else {}, ctx or {})
        if records:
            app.OPLOG.write(records, h._op_device(), erp(app))
    except Exception as e:  # noqa: BLE001
        try:
            app.OPLOG._note_failure(e)
        except Exception:
            pass


def watch_source(body, rec):
    """待改单跟踪记录所属的收货单：优先请求体，其次跟踪记录的来源（有采购单收货优先）。"""
    src, sid = str(body.get("source") or rec.get("source") or ""), _int(body.get("sid") or rec.get("sid"))
    if not sid:
        sources = [g for g in (rec.get("sources") or []) if isinstance(g, dict) and _int(g.get("sid"))]
        pick = next((g for g in sources if g.get("source") == "receipt"), sources[0] if sources else None)
        if pick:
            src, sid = str(pick.get("source") or ""), _int(pick.get("sid"))
    if sid and not src:
        src = "receipt"
    return src, (sid or None)


def _base(ctx, module, op, **kw):
    info = ctx.get("session") or {}
    rec = {"module": module, "op": op, "source": ctx.get("source") or "",
           "sid": ctx.get("sid") or None, "po_id": info.get("po_id", ""),
           "outer_po_id": info.get("outer_po_id", "")}
    rec.update(kw)
    return rec


def line_op(path, action):
    if path in ("/api/item/scan", "/api/item/choose") or action == "scan":
        return "item_scan", "扫码加数"
    if action == "manual":
        return "qty_manual", "手工加数"
    if path == "/api/line/del" or action == "delete_line":
        return "line_delete", "删除明细"
    if path == "/api/batch/del" or action == "delete_batch":
        return "line_delete", "随物流子批次删除明细"
    if path == "/api/batch/copy" or action == "copy_previous":
        return "batch_copy", "复制上一单"
    return "qty_set", "修改数量"


def line_records(app, path, body, ctx, store):
    """scan_audit 增量 → 每次数量变化一条（含操作前 / 操作后）。"""
    start = ctx.get("audit_max")
    if start is None or store is None:
        return None
    try:
        rows = [dict(r) for r in store.db.execute(
            "SELECT * FROM scan_audit WHERE id>? ORDER BY id LIMIT 2000", (start,))]
    except Exception:
        return None
    action = ctx.get("action")
    out = []
    code = str(body.get("code") or "")[:200]
    for r in rows:
        b, a = _qty(r.get("before_qty")) or 0, _qty(r.get("after_qty")) or 0
        if b == a:
            continue
        op, label = line_op(path, action)
        if r.get("action") == "delete" and op not in ("line_delete",):
            op, label = "line_delete", "删除明细"
        info = session_info(store, r.get("sid"), ctx.get("source"))
        out.append({"module": ctx.get("source") or "receipt", "op": op, "source": ctx.get("source") or "",
                    "sid": r.get("sid"), "batch_id": r.get("bid"), "line_id": r.get("lid"),
                    "po_id": info.get("po_id") or r.get("po") or "",
                    "outer_po_id": info.get("outer_po_id") or r.get("outer_no") or "",
                    "tracking_no": r.get("tracking") or "", "sku": r.get("sku") or "",
                    "code": (r.get("raw_code") or code) if op == "item_scan" else "",
                    "qty_before": b, "qty_after": a,
                    "summary": "%s %s：%s → %s（%+d）" % (label, r.get("sku") or "", b, a, a - b),
                    "detail": {"name": r.get("name") or ""}})
    return out


def build(app, path, body, resp, ctx):
    ok = resp.get("ok") is True
    action = ctx.get("action") or ""
    source = ctx.get("source")
    if action in SKIP_ACTIONS:
        return []
    recs = []

    # ---- 入库结果（成功 / 失败都记）
    if ctx.get("inbound"):
        inf = ctx["inbound"]
        job = resp.get("job") if isinstance(resp.get("job"), dict) else {}
        info = inf.get("info") or {}
        status = job.get("status") or ""
        result = "ok" if ok and status in ("complete", "verified", "created", "") else ("fail" if not ok else status)
        if ok and job.get("io_id"):
            summary = "入库单 %s（状态 %s，未自动确认入库）" % (job.get("io_id"), status or "已生成")
        elif ok:
            summary = "入库请求状态：%s" % (status or "未知")
        else:
            summary = "入库失败：" + str(resp.get("msg") or "未知原因")[:300]
        recs.append({"module": "inbound", "op": "inbound_result", "result": result or "ok",
                     "source": inf.get("source"), "sid": inf.get("sid"), "po_id": info.get("po_id"),
                     "outer_po_id": info.get("outer_po_id"), "summary": summary,
                     "detail": {"io_id": job.get("io_id"), "job_id": job.get("id"), "status": status,
                                "error": job.get("error") or ("" if ok else resp.get("msg"))}})
        return recs

    # ---- 登录（失败也记，不含账号密码 / 验证码）
    if path in LOGIN_PATHS:
        method = LOGIN_PATHS[path]
        prev = ctx.get("identity") or ("", "", "")
        if path == "/api/login/restore" and not resp.get("restored"):
            return []
        if ok and resp.get("need_captcha"):
            return []
        if not ok:
            if path in ("/api/login", "/api/sms/login", "/api/qr/finish"):
                return [{"module": "account", "op": "login", "result": "fail",
                         "summary": "登录失败（%s）：%s" % (method, str(resp.get("msg") or "")[:120])}]
            return []
        now = (str(app.JST.uid or ""), str(app.JST.coid or ""))
        switch = bool(prev[0]) and (prev[0], prev[1]) != now
        return [{"module": "account", "op": "account_switch" if switch else "login",
                 "summary": ("切换账号：%s → %s" % (prev[2] or prev[0], app.JST.uname or now[0])) if switch
                            else "登录成功（%s）：%s / %s" % (method, app.JST.uname or now[0], app.JST.coname or now[1]),
                 "detail": {"method": method, "previous_uid": prev[0] if switch else "",
                            "previous_co": prev[1] if switch else ""}}]
    if not ok:
        return []

    if path == "/api/logout":
        prev = ctx.get("identity") or ("", "", "")
        return [{"module": "account", "op": "logout", "summary": "退出登录：%s" % (prev[2] or prev[0] or "未登录"),
                 "detail": {"previous_uid": prev[0], "previous_co": prev[1]}}]
    if path == "/api/account/del":
        return [{"module": "account", "op": "account_delete",
                 "summary": "删除记住的账号：%s" % str(body.get("name") or "")[:60]}]
    if path == "/api/backup/create":
        bk = resp.get("backup") if isinstance(resp.get("backup"), dict) else {}
        files = [f.get("name") for f in (bk.get("files") or []) if isinstance(f, dict)]
        return [{"module": "backup", "op": "backup_create",
                 "summary": "手动创建备份（%d 个数据库）" % len(files), "detail": {"files": files}}]
    if path == "/api/settings":
        before_v = ctx.get("settings") or {}
        changes = {}
        for k in SETTINGS_KEYS:
            try:
                new = app.STORE.get_kv(k, "")
            except Exception:
                continue
            if str(new) != str(before_v.get(k, "")):
                changes[k] = {"before": before_v.get(k, ""), "after": new}
        if not changes:
            return []
        return [{"module": "settings", "op": "settings_change",
                 "summary": "修改设置：" + "，".join("%s %s→%s" % (k, v["before"] or "空", v["after"] or "空")
                                                for k, v in changes.items())[:480],
                 "detail": {"changes": changes}}]
    if path == "/api/remote/config":
        old = ctx.get("remote") or {}
        try:
            c = app.REMOTE.config()
            new = {k: c.get(k) for k in REMOTE_KEYS}
        except Exception:
            new = {}
        changes = {k: {"before": old.get(k), "after": new.get(k)} for k in REMOTE_KEYS if old.get(k) != new.get(k)}
        pw = bool(body.get("password"))
        if not changes and not pw:
            return []
        parts = ["%s %s→%s" % (k, v["before"], v["after"]) for k, v in changes.items()]
        if pw:
            parts.append("已修改管理员密码（不记录密码）")
        return [{"module": "settings", "op": "settings_change", "summary": "远程管理页设置：" + "，".join(parts),
                 "detail": {"changes": changes, "password_changed": pw}}]
    if path == "/api/remote/firewall":
        return [{"module": "settings", "op": "settings_change", "summary": "远程管理页：放行防火墙端口"}]
    if path == "/api/login/preferences":
        prefs = resp.get("preferences") if isinstance(resp.get("preferences"), dict) else {}
        return [{"module": "settings", "op": "settings_change",
                 "summary": "登录偏好：记住账号=%s，保持登录=%s" % (prefs.get("remember_account"), prefs.get("keep_signed_in"))}]
    if path in ("/api/pda/start", "/api/pda/stop", "/api/pda/revoke"):
        label = {"/api/pda/start": "启用 PDA 服务", "/api/pda/stop": "停用 PDA 服务",
                 "/api/pda/revoke": "撤销 PDA 设备 %s" % str(body.get("id") or "")[:40]}[path]
        return [{"module": "pda", "op": "pda_device", "summary": label}]
    if path == "/api/inventory/submit":
        job = resp.get("job") if isinstance(resp.get("job"), dict) else {}
        snap = job.get("snapshot") if isinstance(job.get("snapshot"), dict) else {}
        return [{"module": "inventory", "op": "inventory_submit", "result": "ok" if job.get("status") == "verified" else str(job.get("status") or "ok"),
                 "sku": snap.get("sku_id", ""), "qty_before": _qty(snap.get("old_qty")), "qty_after": _qty(snap.get("target_qty")),
                 "summary": "库存修改 %s：%s → %s（%s）" % (snap.get("sku_id", ""), snap.get("old_qty", ""), snap.get("target_qty", ""), job.get("status", "")),
                 "detail": {"warehouse": snap.get("warehouse"), "owner": snap.get("owner")}}]
    if path == "/api/purchase-watch/action":
        rec = resp.get("record") if isinstance(resp.get("record"), dict) else {}
        label = {"register": "登记待改单跟踪", "delete": "删除待改单跟踪", "archive": "标记跟踪完成",
                 "transition": "修改跟踪状态", "auto": "恢复自动状态"}.get(action)
        if not label:
            return []
        src, wsid = watch_source(body, rec)
        return [{"module": "purchase_watch", "op": "watch", "source": src,
                 "sid": wsid, "po_id": rec.get("po_id", ""),
                 "summary": label + (" → %s" % body.get("state") if action == "transition" else "")
                            + (" 备注：%s" % str(body.get("note"))[:100] if body.get("note") else ""),
                 "detail": {"watch_id": body.get("id") or rec.get("id")}}]

    if not source:
        return []

    # ---- 收货类：明细数量变化（来自触发器流水）
    _, store = source_of(app, path)
    lines = line_records(app, path, body, ctx, store)
    if lines is None and path in ("/api/item/scan", "/api/item/choose") and resp.get("line_id") and not resp.get("duplicate"):
        after_q = _qty(resp.get("qty")) or 0
        added = _qty(resp.get("added")) or 0
        item = resp.get("item") if isinstance(resp.get("item"), dict) else {}
        lines = [_base(ctx, source, "item_scan", line_id=resp.get("line_id"), batch_id=body.get("batch_id"),
                       sku=item.get("sku_id", ""), code=body.get("code", ""), qty_before=after_q - added,
                       qty_after=after_q, summary="扫码加数 %s：%s → %s" % (item.get("sku_id", ""), after_q - added, after_q))]
    recs.extend(lines or [])

    # 收货单信息在操作后可能变化（新建 / 关联采购单），重新取
    sid = _resp_sid(resp, body) or ctx.get("sid")
    if sid and sid != ctx.get("sid") or not ctx.get("session"):
        ctx["sid"] = sid
        ctx["session"] = session_info(store, sid, source) or ctx.get("session") or {}
    elif path in ("/api/unplanned/action", "/api/joint/action") and action in ("match_commit", "refresh", "replace_purchase"):
        ctx["session"] = session_info(store, sid, source) or ctx.get("session") or {}
    module = source
    batch = ctx.get("batch") or {}
    b = body

    def tracking_rec(created=None):
        bt = resp.get("batch") if isinstance(resp.get("batch"), dict) else {}
        no = bt.get("tracking_no") or str(b.get("tracking_no") or "")
        bid = bt.get("id") or resp.get("current_batch") or resp.get("batch_id")
        flag = "" if created is None else ("（新建子批次）" if created else "（已存在，继续该子批次）")
        return _base(ctx, module, "tracking_scan", batch_id=bid, tracking_no=no,
                     summary="扫描物流单号 %s%s" % (no, flag))

    if path in ("/api/po/create", "/api/po/activate"):
        if resp.get("require_new_round_confirmation") or not resp.get("session"):
            return recs
        op = "round_new" if resp.get("reopened") else ("session_resume" if resp.get("resumed") else "session_create")
        recs.append(_base(ctx, module, op, summary="%s：采购单 %s（输入 %s）" % (
            {"round_new": "开始新一轮收货", "session_resume": "继续已有收货批次", "session_create": "创建收货批次"}[op],
            ctx["session"].get("po_id", ""), str(b.get("no") or "")[:60])))
    elif path == "/api/tracking/scan":
        recs.append(tracking_rec(resp.get("created")))
    elif path == "/api/line/edit" or (action == "edit" and path in ("/api/unplanned/action", "/api/joint/action")):
        old = ctx.get("line") or {}
        new = line_info(store, b.get("line_id")) or {}
        changes = {k: {"before": old.get(k), "after": new.get(k)} for k in LINE_FIELDS
                   if k != "qty" and str(old.get(k) or "") != str(new.get(k) or "")}
        if changes:
            recs.append(_base(ctx, module, "line_edit", batch_id=new.get("batch_id") or old.get("batch_id"),
                              line_id=b.get("line_id"), tracking_no=new.get("tracking_no") or old.get("tracking_no") or "",
                              sku=new.get("sku_id") or old.get("sku_id") or "",
                              summary="修改明细资料：" + "，".join("%s %s→%s" % (k, v["before"] or "空", v["after"] or "空")
                                                         for k, v in changes.items())[:450],
                              detail={"changes": changes}))
    elif path == "/api/batch/del" or action == "delete_batch":
        recs.append(_base(ctx, module, "batch_delete", batch_id=batch.get("id") or b.get("batch_id"),
                          tracking_no=batch.get("tracking_no", ""), qty_before=batch.get("total"), qty_after=0,
                          summary="删除物流子批次 %s（%s 条明细，合计 %s 件）" % (
                              batch.get("tracking_no", ""), batch.get("lines", "?"), batch.get("total", "?"))))
    elif path == "/api/batch/rename" or action == "rename":
        recs.append(_base(ctx, module, "batch_rename", batch_id=batch.get("id") or b.get("batch_id"),
                          tracking_no=str(b.get("tracking_no") or ""),
                          summary="修改物流单号：%s → %s" % (batch.get("tracking_no", ""), str(b.get("tracking_no") or "")),
                          detail={"before": batch.get("tracking_no", ""), "after": str(b.get("tracking_no") or "")}))
    elif path == "/api/batch/copy" or action == "copy_previous":
        if resp.get("copied"):
            recs.append(_base(ctx, module, "batch_copy", batch_id=resp.get("current_batch") or b.get("batch_id"),
                              summary="复制上一单（来源物流 %s，共 %s 件）" % (resp.get("copy_source", ""), resp.get("copy_total", ""))))
    elif path in ("/api/session/pause", "/api/session/pause-report"):
        recs.append(_base(ctx, module, "session_pause", summary=("暂存明细并挂起" if path.endswith("report") else "退出 / 挂起批次")))
    elif path == "/api/session/resume":
        recs.append(_base(ctx, module, "session_resume", summary="继续收货" + ("（已刷新采购明细）" if b.get("refresh") is True else "")))
    elif path == "/api/session/close" or action == "close_session":
        recs.append(_base(ctx, module, "session_close", summary="结单"))
    elif path == "/api/session/del" or action == "delete_session":
        st = resp.get("stat") if isinstance(resp.get("stat"), dict) else {}
        recs.append(_base(ctx, module, "session_delete", summary="删除收货单" + (
            "（%s 个子批次 / %s 条明细）" % (st.get("batches"), st.get("lines")) if st else "")))
    elif path in ("/api/unplanned/action", "/api/joint/action"):
        if action == "new":
            label = "新建无采购单收货批次" if source == "unplanned" else "新建联合收货批次（母单 %s）" % str(b.get("mother") or "")[:60]
            recs.append(_base(ctx, module, "session_create", tracking_no=str(b.get("tracking_no") or ""), summary=label))
            if b.get("tracking_no"):
                recs.append(tracking_rec(True))
        elif action == "tracking":
            recs.append(tracking_rec())
        elif action in ("match_commit", "refresh", "replace_purchase"):
            recs.append(_base(ctx, module, "purchase_link", summary={
                "match_commit": "关联采购单 %s" % ctx["session"].get("po_id", ""),
                "refresh": "刷新采购单明细 / 对照",
                "replace_purchase": "更换联合批次采购单"}[action]))
        elif action in ("plan", "cancel"):
            recs.append(_base(ctx, module, "allocation", summary="分配到采购单" if action == "plan" else "取消分配 %s" % b.get("stage_sid", ""),
                              detail={"stage_sid": b.get("stage_sid")}))
        elif action == "finish_package":
            recs.append(_base(ctx, module, "package_finish", batch_id=b.get("batch_id"),
                              summary="标记物流整件%s" % ("完成" if b.get("finished") else "未完成")))
        elif action in ("context", "note"):
            recs.append(_base(ctx, module, "note", summary="修改货主 / 分仓范围" if action == "context" else "修改批次备注"))
    return recs


def safe_body(body):
    """调试用：去掉敏感键后的请求体。"""
    if not isinstance(body, dict):
        return {}
    return {k: v for k, v in body.items() if not is_secret_key(k)}
