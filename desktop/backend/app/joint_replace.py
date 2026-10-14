"""Explicit one-purchase correction. Preview/commit never write to the ERP."""
import hashlib
import hmac
import json
import secrets

from inbound import json_text, quantity
from jst import normalize_po_item
from store import now_str
from unplanned import clean

_KEY = secrets.token_bytes(32)


def replace_purchase(service, body):
    sid, pid = int(body.get('sid') or 0), int(body.get('purchase_id') or 0)
    commit = body['action'] == 'replace_purchase'
    service.guard(sid, body.get('revision'), reconciliation=True)
    service.purchase(sid, pid)
    no = clean(body.get('purchase_no'), 200)
    if not no:
        raise ValueError('请输入新的采购单号或外部单号')
    # Remote lookup is read-only, before the local transaction. Commit reads again.
    new = service.resolve_po(no)
    db = service.store.db
    db.execute('BEGIN IMMEDIATE' if commit else 'BEGIN')
    try:
        service.guard(sid, body.get('revision'), reconciliation=True)
        old = service.purchase(sid, pid)
        if old['po_id'] == new['po_id']:
            raise ValueError('新旧采购单相同，无需替换；更新明细请使用刷新')
        if (old['owner'], old['auth']) != (new['owner'], new['auth']):
            raise ValueError('新采购单必须与本组同货主、同仓库公司')
        purchases = [dict(r) for r in db.execute('SELECT * FROM joint_purchase WHERE group_sid=? ORDER BY id', (sid,))]
        if any(p['po_id'] == new['po_id'] for p in purchases):
            raise ValueError('新采购单已在当前联合批次中，不能重复加入')
        scopes = {p['id']: {str(r.get('sku_id') or '') for r in (new['raw'] if p['id'] == pid else json.loads(p['live_json']))} for p in purchases}
        stages = [dict(r) for r in db.execute('SELECT * FROM joint_stage WHERE group_sid=? ORDER BY sid', (sid,))]
        affected = []
        for stage in stages:
            jobs = service.store.inbound_jobs(stage['sid'])
            # Even rejected or cancelled historical submissions must keep their association.
            if stage['purchase_id'] == pid and jobs:
                raise ValueError('原采购单存在入库记录（含失败 / 待核实），不可替换关联；请核对并继续原单')
            if stage['cancelled']:
                continue
            lines = [dict(r) for r in db.execute('SELECT a.source_line,a.qty,l.sku_id,l.name,b.tracking_no FROM joint_allocation a JOIN line l ON l.id=a.source_line JOIN batch b ON b.id=l.batch_id WHERE a.stage_sid=? ORDER BY a.id', (stage['sid'],))]
            invalid = stage['purchase_id'] == pid
            for line in lines:
                scope = [k for k, skus in scopes.items() if line['sku_id'] in skus]
                invalid |= bool(scope and stage['purchase_id'] not in scope or not scope and stage['mode'] == 'purchase_only')
            if not invalid:
                continue
            if jobs:
                raise ValueError('替换会影响其他采购单已提交的分配，不可替换；请先核对原入库记录')
            affected.append(dict(stage_sid=stage['sid'], po_id=service.store.get_session(stage['sid'])['po_id'], lines=lines,
                                 total=float(sum((quantity(r['qty']) for r in lines), quantity(0)))))
        received = [dict(r) for r in db.execute('SELECT * FROM line WHERE session_id=? ORDER BY id', (sid,))]
        snapshot = dict(account=service.account(), sid=sid, purchase_id=pid, revision=body['revision'], purchases=purchases,
                        new=new, stages=stages, affected=affected, received=received)
        token = hmac.new(_KEY, json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode(), hashlib.sha256).hexdigest()
        preview = dict(purchase_id=pid, old_po_id=old['po_id'], new_po_id=new['po_id'], head=new['head'],
                       owner=new['owner'], auth=new['auth'], old_items=json.loads(old['initial_json']), new_items=new['raw'],
                       affected=affected, received_total=sum(float(r['qty']) for r in received), fingerprint=token)
        if not commit:
            db.rollback()
            return dict(replace_preview=preview)
        if body.get('confirmed') is not True or not hmac.compare_digest(token, str(body.get('fingerprint') or '')):
            raise ValueError('采购资料或本地分配已变化，或确认已失效，请重新预览后替换')
        if affected and body.get('cancel_affected') is not True:
            raise ValueError('请明确勾选取消受影响的未提交分配；原实收不会删除')
        at = now_str()
        for stage in affected:
            db.execute('UPDATE joint_stage SET cancelled=1 WHERE sid=?', (stage['stage_sid'],))
        db.execute('UPDATE joint_purchase SET po_id=?,head_json=?,initial_json=?,live_json=?,read_at=? WHERE id=? AND group_sid=?',
                   (new['po_id'], json_text(new['head']), json_text(new['raw']), json_text(new['raw']), at, pid, sid))
        items = [normalize_po_item(r) for p in purchases for r in (new['raw'] if p['id'] == pid else json.loads(p['live_json']))]
        db.execute('UPDATE session SET items_json=? WHERE id=?', (json_text(items), sid))
        db.execute('UPDATE unplanned_meta SET revision=revision+1,checked_revision=-1 WHERE sid=?', (sid,))
        # Full before/after retained, including cancelled stage ids; no helper with implicit commit.
        db.execute('INSERT INTO joint_audit(group_sid,at,action,detail) VALUES(?,?,?,?)',
                   (sid, at, '逐张替换采购单并重新匹配', json_text(dict(old=old, new=new, affected=affected))))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return dict(service.state(sid), replaced=True, old_po_id=old['po_id'], new_po_id=new['po_id'])
