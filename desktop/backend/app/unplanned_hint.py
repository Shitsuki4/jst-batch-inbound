"""Read-only lookup: is a tracking number already registered in 无采购单收货?

Used by the ordinary (有采购单) tracking scan to warn before a parcel is received
twice. It opens unplanned.db with its own read-only SQLite connection, so it
never initializes the UnplannedService, never writes, and never takes any lock
of its own (the caller already serializes requests with state_lock).

Only two situations are reported:
  * ``unlinked``: the record has no purchase order yet and has not been
    submitted for inbound (no non-rejected inbound_job);
  * ``same_po``: the record is already linked to the very PO being received.
Records linked to a different PO are ignored on purpose (keep it simple).
"""
import os
import sqlite3
import unicodedata
from pathlib import Path

MAX_MATCHES = 5


def norm(value):
    """Exact-match key: NFKC, whitespace removed, case-insensitive."""
    return ''.join(unicodedata.normalize('NFKC', str(value or '')).split()).casefold()


def _connect(path):
    uri = Path(path).resolve().as_uri()
    try:
        db = sqlite3.connect(uri + '?mode=ro', uri=True, timeout=1.5)
    except sqlite3.OperationalError:
        # Some WAL setups refuse a pure read-only open; never create the file, never write.
        db = sqlite3.connect(uri + '?mode=rw', uri=True, timeout=1.5)
        db.execute('PRAGMA query_only=ON')
    db.row_factory = sqlite3.Row
    return db


def _status(state, kind):
    if kind == 'same_po':
        return '已绑定本采购单' + ('（已结单）' if state == 'closed' else '')
    return '已结单，未绑定采购单' if state == 'closed' else '登记中，未绑定采购单'


def lookup(path, tracking_no, po_id=''):
    """Return a hint dict, or None when nothing relevant is registered.

    Raises on database errors; callers decide (the server logs and continues).
    """
    key = norm(tracking_no)
    if not key or not path or not os.path.exists(path):
        return None
    po_id = str(po_id or '').strip()
    db = _connect(path)
    try:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'session', 'batch', 'line', 'unplanned_meta'} <= tables:
            return None
        has_jobs = 'inbound_job' in tables
        rows = db.execute(
            'SELECT b.id AS batch_id, b.tracking_no, b.first_scan_at, s.id AS sid, s.input_no, '
            "COALESCE(s.po_id,'') AS po_id, s.state, s.started_at, m.note "
            'FROM batch b JOIN session s ON s.id=b.session_id JOIN unplanned_meta m ON m.sid=s.id '
            'ORDER BY b.id DESC').fetchall()
        matches = []
        for r in rows:
            if norm(r['tracking_no']) != key:
                continue
            linked = str(r['po_id'] or '').strip()
            if not linked:
                if has_jobs and db.execute("SELECT 1 FROM inbound_job WHERE session_id=? AND status<>'rejected' LIMIT 1",
                                           (r['sid'],)).fetchone():
                    continue  # already submitted for inbound: not a pending registration
                kind = 'unlinked'
            elif po_id and linked == po_id:
                kind = 'same_po'
            else:
                continue
            qty, skus = db.execute('SELECT COALESCE(SUM(qty),0), COUNT(DISTINCT sku_id) FROM line '
                                   'WHERE batch_id=? AND qty>0', (r['batch_id'],)).fetchone()
            matches.append(dict(kind=kind, sid=r['sid'], batch_id=r['batch_id'], input_no=r['input_no'],
                                tracking_no=r['tracking_no'], registered_at=r['first_scan_at'] or r['started_at'] or '',
                                qty=float(qty or 0), sku_count=int(skus or 0), state=r['state'],
                                status=_status(r['state'], kind), po_id=linked, note=r['note'] or ''))
            if len(matches) >= MAX_MATCHES:
                break
    finally:
        db.close()
    if not matches:
        return None
    # The more serious case (same PO already linked) is shown first.
    matches.sort(key=lambda m: 0 if m['kind'] == 'same_po' else 1)
    first = matches[0]
    detail = '（登记时间 %s，%s 件 / %d 个SKU，当前状态：%s）' % (
        first['registered_at'] or '未知', _num(first['qty']), first['sku_count'], first['status'])
    if first['kind'] == 'same_po':
        message = '该物流单号已在「无采购单收货」登记%s，并已绑定本采购单，请勿重复收货。' % detail
    else:
        message = ('该物流单号已在「无采购单收货」登记%s，尚未绑定采购单。'
                   '建议到无采购单收货中绑定本采购单，避免重复收货。' % detail)
    if len(matches) > 1:
        message += '（共 %d 条相关登记）' % len(matches)
    return dict(kind=first['kind'], message=message, record=first, matches=matches,
                open_url='/unplanned.html?sid=%d' % int(first['sid']))


def _num(value):
    return str(int(value)) if float(value).is_integer() else str(value)
