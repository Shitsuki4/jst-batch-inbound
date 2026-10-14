"""Immutable first retained purchase snapshot; live receipts are never frozen."""
import json
from decimal import Decimal, InvalidOperation


def migrate(store):
    store.db.executescript("""
    CREATE TABLE IF NOT EXISTS purchase_initial (
      po_id TEXT NOT NULL, owner_co_id TEXT NOT NULL, auth_co_id TEXT NOT NULL,
      items_json TEXT, captured_at TEXT, source TEXT NOT NULL, source_session_id INTEGER,
      PRIMARY KEY(po_id, owner_co_id, auth_co_id));
    CREATE TRIGGER IF NOT EXISTS purchase_initial_no_update BEFORE UPDATE ON purchase_initial
      BEGIN SELECT RAISE(ABORT, '初始采购快照不可覆盖'); END;
    CREATE TRIGGER IF NOT EXISTS purchase_initial_no_delete BEFORE DELETE ON purchase_initial
      BEGIN SELECT RAISE(ABORT, '初始采购快照不可删除'); END;
    """)
    # Never use refreshed items_json as a supposedly historical first snapshot.
    for s in store.db.execute("SELECT * FROM session ORDER BY id").fetchall():
        capture(store, dict(s), s['round_baseline_json'], s['started_at'], 'earliest_retained_round', s['id'])
    store.db.commit()


def capture(store, session, items_json, captured_at, source, sid=None):
    store.db.execute("INSERT OR IGNORE INTO purchase_initial VALUES(?,?,?,?,?,?,?)",
        (str(session.get('po_id') or ''), str(session.get('owner_co_id') or ''),
         str(session.get('auth_co_id') or ''), items_json, captured_at, source, sid))


def number(value):
    if value is None or isinstance(value, bool): return None
    try:
        n = Decimal(str(value))
        return n if n.is_finite() else None
    except (InvalidOperation, ValueError): return None


def compare(store, sid, items):
    """Exact SKU aggregation, including old RAW keys, without altering stored lines."""
    planned, received = {}, {}
    for i, item in enumerate(items):
        sku = str(item.get('sku_id') or '').strip()
        key = sku or '__missing_sku:' + str(i)
        row = planned.setdefault(key, dict(item, sku_id=sku, total=Decimal(0)))
        qty = number(item.get('qty')) if item.get('qty_known') is not False and sku else None
        row['total'] = row['total'] + qty if row['total'] is not None and qty is not None else None
    for item in store.db.execute('SELECT sku_id,sku_code,name,spec,qty FROM line WHERE session_id=? ORDER BY id', (sid,)):
        key = str(item['sku_id'] or '').strip()
        row = received.setdefault(key, dict(item, total=Decimal(0)))
        row['total'] += number(item['qty']) or Decimal(0)
    rows = []
    for key in list(planned) + [k for k in received if k not in planned]:
        plan, got = planned.get(key), received.get(key)
        meta = plan or got
        target = plan['total'] if plan else Decimal(0)
        qty = got['total'] if got else Decimal(0)
        delta = qty - target if target is not None else None
        done = delta is not None and abs(delta) < Decimal('0.000001')
        r = {k: meta.get(k) for k in ('sku_id', 'sku_code', 'name', 'spec')}
        r.update(plan=target, ordered=target, recv=qty, diff=delta, done=done,
                 over=delta is not None and delta > 0, extra=not bool(plan),
                 reason='待核实' if delta is None else ('收齐' if done else (('多收 %g' % delta) if delta > 0 else ('少收 %g' % -delta))))
        rows.append({k: float(v) if isinstance(v, Decimal) else v for k, v in r.items()})
    return rows


def initial_view(store, sid):
    s = store.get_session(sid) or {}
    found = store.db.execute('SELECT * FROM purchase_initial WHERE po_id=? AND owner_co_id=? AND auth_co_id=?',
        (str(s.get('po_id') or ''), str(s.get('owner_co_id') or ''), str(s.get('auth_co_id') or ''))).fetchone()
    result = {'available': False, 'rows': [], 'captured_at': None, 'source': 'missing',
              'notice': '未保存可恢复的初始采购快照；不会用当前采购明细冒充首次数据。'}
    if not found: return result
    result.update({k: found[k] for k in ('captured_at', 'source', 'source_session_id')})
    try:
        items = json.loads(found['items_json'])
        if not isinstance(items, list) or not all(isinstance(x, dict) for x in items): return result
    except (TypeError, ValueError): return result
    result.update(available=True, rows=compare(store, sid, items), notice=(
        '升级前最早保留收货轮次的起始快照；更早已删除的记录无法恢复。' if found['source'] == 'earliest_retained_round' else
        '首次成功查询并建立收货记录时保存；后续采购改单、刷新和新收货轮次不会覆盖。'))
    return result


def live_view(store, sid):
    s = store.get_session(sid) or {}
    return compare(store, sid, json.loads(s.get('items_json') or '[]'))
