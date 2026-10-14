"""Copy a previous parcel locally, never submit an ERP inbound order.

Preview is read-only. Confirmation rechecks the complete snapshot under a SQLite
write lock. Lines, provenance, scoped preference and revision commit together.
"""
import hashlib
import hmac
import json
import math
import secrets

from store import now_str

_KEY = secrets.token_bytes(32)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def copy_previous(store, body, *, account, guard, validate, after_write=None):
    sid, bid = int(body.get('sid') or 0), int(body.get('batch_id') or 0)
    action = body.get('action')
    if action not in ('copy_preview', 'copy_previous'):
        raise ValueError('不支持的复制操作')
    db = store.db
    # The callers do not perform writes before this operation.
    db.execute('BEGIN IMMEDIATE' if action == 'copy_previous' else 'BEGIN')
    try:
        guard()
        session = store.get_session(sid)
        if not session or session['state'] == 'closed':
            raise ValueError('当前收货批次不存在或已结单')
        target = db.execute('SELECT * FROM batch WHERE id=? AND session_id=?', (bid, sid)).fetchone()
        if not target:
            raise ValueError('目标物流单与当前采购单 / 收货批次不匹配')
        if db.execute('SELECT 1 FROM inbound_batch WHERE batch_id=?', (bid,)).fetchone():
            raise ValueError('目标物流已提交或锁定，不能复制商品')
        if db.execute('SELECT 1 FROM line WHERE batch_id=?', (bid,)).fetchone():
            raise ValueError('当前物流单已有商品，不能复制，避免重复登记；请新建空物流单')
        if db.execute('SELECT 1 FROM receipt_copy_log WHERE target_bid=?', (bid,)).fetchone():
            raise ValueError('此物流单已复制过商品，不能重复复制，请核对实收记录')
        # Previous registered parcel in THIS session, not the last viewed parcel
        # or a client supplied source id. Do not silently skip empty parcels.
        source = db.execute('SELECT * FROM batch WHERE session_id=? AND id<? ORDER BY id DESC LIMIT 1', (sid, bid)).fetchone()
        if not source:
            raise ValueError('当前采购单 / 收货批次没有上一单可复制')
        rows = [dict(r) for r in db.execute('SELECT * FROM line WHERE session_id=? AND batch_id=? ORDER BY id', (sid, source['id']))]
        if not rows:
            raise ValueError('上一物流单没有商品，不能复制；请先核对上一单')
        if any(not r['sku_id'] or not math.isfinite(float(r['qty'])) or not 0 < float(r['qty']) <= 100000000 for r in rows):
            raise ValueError('上一单存在无效商品编码或数量（须大于0且不超过一亿），请先修正')
        validate(session, rows)
        scope = _json([account, sid, session['po_id'], session['owner_co_id'], session['auth_co_id'], session['id_type']])
        skip = bool(db.execute('SELECT 1 FROM receipt_copy_preference WHERE scope=?', (scope,)).fetchone())
        snapshot = dict(scope=scope, session=session, source=dict(source), target=dict(target), rows=rows, revision=body.get('revision'))
        token = hmac.new(_KEY, _json(snapshot).encode('utf-8'), hashlib.sha256).hexdigest()
        preview = dict(source_batch_id=source['id'], source_tracking=source['tracking_no'],
                       target_batch_id=bid, target_tracking=target['tracking_no'],
                       lines=[{k: r[k] for k in ('sku_id', 'sku_code', 'name', 'spec', 'qty', 'matched')} for r in rows],
                       total=sum(float(r['qty']) for r in rows), fingerprint=token, skip_confirmation=skip)
        if action == 'copy_preview':
            db.rollback()
            return dict(copy_preview=preview)
        if body.get('confirmed') is not True or not hmac.compare_digest(token, str(body.get('fingerprint') or '')):
            raise ValueError('来源单号、商品或数量已变化，或确认已失效，请重新预览后复制')
        at = now_str()
        for r in rows:
            db.execute('INSERT INTO line(session_id,batch_id,poi_id,key,sku_id,i_id,sku_code,name,spec,qty,matched,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       (sid, bid, r['poi_id'], r['key'], r['sku_id'], r['i_id'], r['sku_code'], r['name'], r['spec'], r['qty'], r['matched'], at))
        db.execute('INSERT INTO receipt_copy_log(target_bid,sid,source_bid,source_tracking,target_tracking,snapshot_json,at) VALUES(?,?,?,?,?,?,?)',
                   (bid, sid, source['id'], source['tracking_no'], target['tracking_no'], _json(preview), at))
        if body.get('dont_prompt') is True:
            db.execute('INSERT OR IGNORE INTO receipt_copy_preference(scope) VALUES(?)', (scope,))
        if after_write:
            after_write()  # SQL only; must not commit independently.
        db.commit()
        return dict(copied=True, copy_source=source['tracking_no'], copy_total=preview['total'], current_batch=bid)
    except Exception:
        db.rollback()
        raise
