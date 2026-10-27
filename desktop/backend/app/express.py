"""快递单登记：扫码流程与「无采购单登记」相同（先扫物流单号，再扫商品 / 数量），但完全不关联采购单。

* 独立数据库 express.db（不与 unplanned.db 混存）。
* 不读取、不写入任何采购单：采购相关操作一律拒绝；客户端的采购明细接口被屏蔽。
* 导出每个物流单号一行：单号 | 扫描时间 | 明细。扫描时间 = 物流单号扫描登记时间（batch.first_scan_at），
  明细与无采购单登记「复制明细文字」的格式相同（商品编码 商品名称 数量，多行放在同一单元格）。
"""
import csv
import io
import re

from store import now_str
from unplanned import UnplannedService, clean, scan_time_text, stored_qty

SOURCE = '快递单'
# 采购单相关（关联、核对、生成入库单）——快递单登记一律不支持。
PURCHASE_ACTIONS = frozenset({'match', 'refresh', 'match_search', 'match_preview', 'match_commit',
                              'replace_purchase', 'preview', 'create', 'resume', 'options'})
ALLOWED_ACTIONS = frozenset({'new', 'close_session', 'delete_session', 'context', 'note', 'tracking', 'rename',
                             'delete_batch', 'scan', 'manual', 'edit', 'delete_line', 'resolve',
                             'copy_preview', 'copy_previous'})
NO_PURCHASE = '快递单登记不关联采购单，不支持此操作'
DETAIL_HEADER = '商品编码 商品名称 数量'


def _no_purchase(*_args, **_kwargs):
    raise ValueError(NO_PURCHASE)


class NoPurchaseClient:
    """只放行商品资料 / 账号信息；采购单明细等接口直接拒绝，保证快递单登记不会触达采购单。"""
    BLOCKED = ('fetch_detail', 'purchase', 'po_', 'inbound', 'add_items', 'confirm')

    def __init__(self, client):
        object.__setattr__(self, '_client', client)

    def __getattr__(self, name):
        if any(word in name.lower() for word in self.BLOCKED):
            return _no_purchase
        return getattr(self._client, name)


def _clean_text(value):
    return re.sub(r'\s+', ' ', '' if value is None else str(value)).strip()


def qty_text(value):
    """与前端 String(Number(x)) 一致：整数不带 .0。"""
    n = stored_qty(value)
    return str(int(n)) if float(n).is_integer() else repr(float(n))


def detail_text(lines):
    """一张物流单的明细文字，格式与无采购单登记「复制明细文字」相同；没有实收商品时返回空字符串。"""
    rows = [l for l in lines if stored_qty(l.get('qty')) > 0]
    if not rows:
        return ''
    return '\n'.join([DETAIL_HEADER] + [_clean_text(l.get('sku_id')) + ' ' + _clean_text(l.get('name')) + ' ' + qty_text(l.get('qty'))
                                         for l in rows])


class ExpressService(UnplannedService):
    def __init__(self, path, client, lookup, scan_filter=None, learned=None):
        super().__init__(path, NoPurchaseClient(client), _no_purchase, lookup, scan_filter=scan_filter, learned=learned)
        self.inbound = None  # 快递单登记不生成入库单

    def session(self, sid):
        s = self.store.get_session(int(sid))
        if not s:
            raise ValueError('找不到快递单登记批次')
        return s

    def new(self, note, tracking_no='', owner='', auth=''):
        owner, auth = self.context(owner, auth)
        stamp = now_str()
        with self.store.db:
            c = self.store.db.execute('INSERT INTO session(input_no,po_id,id_type,state,started_at,items_json,round_baseline_json,owner_co_id,auth_co_id) VALUES(?,?,?,?,?,?,?,?,?)',
                                      ('', '', 'express', 'active', stamp, '[]', '[]', owner, auth))
            sid = c.lastrowid
            self.store.db.execute('UPDATE session SET input_no=? WHERE id=?', ('KD' + stamp[:10].replace('-', '') + '-' + str(sid).zfill(4), sid))
            self.store.db.execute('INSERT INTO unplanned_meta(sid,note,catalog_owner,catalog_auth) VALUES(?,?,?,?)', (sid, clean(note), owner, auth))
        result = self.state(sid)
        if tracking_no:
            batch, _ = self.store.ensure_batch(sid, clean(tracking_no, 200))
            result = self.state(sid)
            result['current_batch'] = batch['id']
        return result

    def mutate(self, action, body, *, after_copy=None):
        if action in PURCHASE_ACTIONS or action not in ALLOWED_ACTIONS:
            raise ValueError(NO_PURCHASE if action in PURCHASE_ACTIONS else '不支持的操作')
        return super().mutate(action, body, after_copy=after_copy)

    def history(self, query='', kind='all'):
        rows = super().history(query, 'tracking' if kind == 'tracking' else 'all')
        for r in rows:
            for k in ('po_id', 'outer_po_id', 'supplier'):
                r.pop(k, None)
        return rows

    def waybills(self, state):
        started = state['session'].get('started_at')
        return [{'id': b['id'], 'tracking_no': b['tracking_no'], 'scan_at': scan_time_text(b.get('first_scan_at'), started),
                 'total': b.get('total', 0), 'kinds': b.get('kinds', 0), 'detail': detail_text(b['lines'])}
                for b in state['batches']]

    def state(self, sid):
        result = super().state(sid)
        # 快递单登记永远不关联采购单：不返回应收、核对或入库能力。
        result.update(linked=False, can_create=False, checked=False, problems=[], jobs=[])
        result['waybills'] = self.waybills(result)
        return result

    def export_rows(self, sid):
        return [[w['tracking_no'], w['scan_at'], w['detail']] for w in self.state(sid)['waybills']]

    def export(self, sid):
        out = io.StringIO(newline='')
        w = csv.writer(out)

        def safe(v):
            if isinstance(v, str) and v.startswith(('=', '+', '-', '@', chr(9), chr(13))):
                return "'" + v
            return v
        w.writerow(['单号', '扫描时间', '明细'])
        for row in self.export_rows(sid):
            w.writerow([safe(v) for v in row])
        return ('\ufeff' + out.getvalue()).encode('utf-8')
