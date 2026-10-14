"""Local-only close/delete for special receipt desks; no ERP/media deletion."""
import json
from store import now_str


def finish_local(store, body, state, related_sids=(), cleanup=None):
    sid = int(body.get('sid') or 0)
    action = body.get('action')
    if action not in ('close_session', 'delete_session') or body.get('confirmed') is not True:
        raise ValueError('请明确确认结单 / 删除本地收货记录')
    db = store.db
    db.execute('BEGIN IMMEDIATE')
    try:
        s = store.get_session(sid)
        meta = db.execute('SELECT revision FROM unplanned_meta WHERE sid=?', (sid,)).fetchone()
        if not s or not meta or body.get('revision') is None or int(body['revision']) != meta['revision']:
            raise ValueError('收货记录已变化，请刷新后重新核对')
        if body.get('expected_session_no') != s['input_no']:
            raise ValueError('当前批次与确认信息不匹配，请重新确认')
        if action == 'close_session':
            if s['state'] == 'closed': raise ValueError('该批次已经结单')
            snapshot = state(sid)
            at = now_str()
            db.execute('INSERT INTO receipt_close_snapshot VALUES(?,?,?)', (sid, at, json.dumps(snapshot, ensure_ascii=False, allow_nan=False)))
            db.execute('UPDATE session SET state="closed",ended_at=? WHERE id=?', (at, sid))
            db.execute('UPDATE unplanned_meta SET revision=revision+1 WHERE sid=?', (sid,))
            db.commit()
            return state(sid)
        ids = [sid, *related_sids]
        for current in ids:
            # Includes rejected/unknown submissions, like ordinary receipt deletion.
            if store.inbound_jobs(current): raise ValueError('此批次存在入库记录（含失败 / 待核实记录），不可删除；请核对原单')
        counts = dict(batches=db.execute('SELECT COUNT(*) FROM batch WHERE session_id=?',(sid,)).fetchone()[0],
                      lines=db.execute('SELECT COUNT(*) FROM line WHERE session_id=?',(sid,)).fetchone()[0])
        if cleanup: cleanup()
        for current in ids:
            db.execute('DELETE FROM line WHERE session_id=?', (current,))
            db.execute('DELETE FROM batch WHERE session_id=?', (current,))
            db.execute('DELETE FROM session WHERE id=?', (current,))
            db.execute('DELETE FROM unplanned_meta WHERE sid=?', (current,))
            db.execute('DELETE FROM receipt_close_snapshot WHERE sid=?', (current,))
        db.commit()
        return dict(deleted=True, **counts, message='已删除本地收货记录；未删除录像、未修改采购单或ERP库存')
    except Exception:
        db.rollback()
        raise
