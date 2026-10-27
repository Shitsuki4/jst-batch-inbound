"""快递单登记：独立存储、不触达采购单、导出 单号 | 扫描时间 | 明细。"""
import csv, io, json, os, tempfile, unittest
from express import ExpressService, detail_text, NO_PURCHASE
from inbound_test import FakeJst


class TrackingJst(FakeJst):
    def __init__(self):
        super().__init__()
        self.purchase_calls = []

    def fetch_detail(self, po_id, owner, auth):
        self.purchase_calls.append(('fetch_detail', po_id))
        raise AssertionError('快递单登记不应读取采购单')


class ExpressTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.j = TrackingJst()
        self.path = os.path.join(self.tmp.name, 'express.db')
        self.s = ExpressService(self.path, self.j, lambda *a, **k: [])
        self.sid = self.s.new('快递', owner='OWNER', auth='WAREHOUSE')['session']['id']
        self.bid = 0

    def tearDown(self):
        self.s.store.db.close()
        self.tmp.cleanup()

    def change(self, a, **kw):
        return self.s.mutate(a, dict(sid=self.sid, batch_id=self.bid, revision=self.s.state(self.sid)['meta']['revision'], **kw))

    def batch(self, no):
        self.bid = self.change('tracking', tracking_no=no)['current_batch']
        return self.bid

    def add(self, sku, n, name=None):
        return self.change('manual', sku_id=sku, name=name or ('商品' + sku), sku_code='', spec='规格', qty=n)

    def receive(self):
        self.batch('123'); self.add('A', 5); self.add('B', 8); self.add('A', 2)
        self.batch('SF002'); self.add('C', 1, name='名称  带\u3000空白')
        self.batch('EMPTY')

    def test_export_one_row_per_waybill_with_scan_time_and_multiline_detail(self):
        self.receive()
        db = self.s.store.db
        db.execute("UPDATE batch SET first_scan_at='2026-10-17 20:46:53' WHERE tracking_no='123'")
        db.execute("UPDATE batch SET first_scan_at='2026-10-17 21:00:00' WHERE tracking_no='SF002'")
        db.execute("UPDATE batch SET first_scan_at='' WHERE tracking_no='EMPTY'")
        db.execute("UPDATE session SET started_at='2026-10-17 08:00:00' WHERE id=?", (self.sid,))
        db.commit()
        raw = self.s.export(self.sid)
        self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))
        rows = list(csv.reader(io.StringIO(raw.decode('utf-8-sig'))))
        self.assertEqual(rows[0], ['单号', '扫描时间', '明细'])
        self.assertEqual(rows[1], ['123', '2026/10/17 20:46:53', '商品编码 商品名称 数量\nA 商品A 7\nB 商品B 8'])
        self.assertEqual(rows[2], ['SF002', '2026/10/17 21:00:00', '商品编码 商品名称 数量\nC 名称 带 空白 1'])
        self.assertEqual(rows[3], ['EMPTY', '2026/10/17 08:00:00', ''])
        self.assertEqual(len(rows), 4)

    def test_detail_matches_unplanned_copy_text_format(self):
        # 无采购单登记「复制明细文字」：首行「商品编码 商品名称 数量」，每个商品一行「编码 名称 数量」，数量为0不列出。
        lines = [dict(sku_id='A', name='商品 A', qty=3.0), dict(sku_id='Z', name='零', qty=0), dict(sku_id='B', name='B\n名', qty=12)]
        self.assertEqual(detail_text(lines), '商品编码 商品名称 数量\nA 商品 A 3\nB B 名 12')
        self.assertEqual(detail_text([]), '')

    def test_state_has_waybills_for_left_panel(self):
        self.receive()
        st = self.s.state(self.sid)
        self.assertEqual([w['tracking_no'] for w in st['waybills']], ['123', 'SF002', 'EMPTY'])
        self.assertRegex(st['waybills'][0]['scan_at'], r'^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}$')
        self.assertFalse(st['linked']); self.assertFalse(st['can_create'])
        self.assertTrue(st['session']['input_no'].startswith('KD'))
        self.assertEqual(st['session']['id_type'], 'express')

    def test_never_touches_purchase_orders(self):
        self.receive()
        for action in ('match', 'refresh', 'match_search', 'match_preview', 'match_commit', 'replace_purchase', 'preview', 'create', 'resume'):
            with self.assertRaisesRegex(ValueError, NO_PURCHASE):
                self.change(action, po_id='PO123')
        with self.assertRaises(ValueError):
            self.s.client.fetch_detail('PO123', 'OWNER', 'WAREHOUSE')
        with self.assertRaises(ValueError):
            self.s.find_purchase('PO123')
        self.assertIsNone(self.s.inbound)
        self.assertEqual(self.j.purchase_calls, [])
        s = self.s.session(self.sid)
        self.assertEqual((s['po_id'], s['items_json']), ('', '[]'))
        self.assertTrue(all(w[1] != 'fetch_detail' for w in self.j.calls))
        self.assertEqual(self.s.store.db.execute('SELECT COUNT(*) FROM inbound_job').fetchone()[0], 0)
        self.assertEqual(self.s.store.db.execute("SELECT COUNT(*) FROM line WHERE poi_id<>'' OR matched<>0").fetchone()[0], 0)

    def test_own_storage_not_mixed_with_unplanned(self):
        from unplanned import UnplannedService
        self.receive()
        other = UnplannedService(os.path.join(self.tmp.name, 'unplanned.db'), FakeJst(), lambda *a, **k: ({}, {}), lambda *a, **k: [])
        try:
            self.assertEqual(other.history(), [])
            self.assertEqual(len(self.s.history()), 1)
            self.assertEqual(len(self.s.history('SF002', 'tracking')), 1)
        finally:
            other.store.db.close()

    def test_rename_delete_close_and_scan_time_kept(self):
        bid = self.batch('OLD-1'); self.add('A', 1)
        at = self.s.store.db.execute('SELECT first_scan_at FROM batch WHERE id=?', (bid,)).fetchone()[0]
        self.change('rename', tracking_no='NEW-1')
        st = self.s.state(self.sid)
        self.assertEqual(st['waybills'][0]['tracking_no'], 'NEW-1')
        self.assertEqual(self.s.store.db.execute('SELECT first_scan_at FROM batch WHERE id=?', (bid,)).fetchone()[0], at)
        self.change('close_session', confirmed=True, expected_session_no=st['session']['input_no'])
        self.assertEqual(self.s.session(self.sid)['state'], 'closed')
        with self.assertRaises(ValueError):
            self.add('B', 1)

    def test_new_requires_tracking_and_creates_batch(self):
        r = self.s.mutate('new', dict(tracking_no='FIRST-1', note='', owner_co_id='OWNER', auth_co_id='WAREHOUSE'))
        self.assertEqual(r['batches'][0]['tracking_no'], 'FIRST-1')
        self.assertEqual(r['current_batch'], r['batches'][0]['id'])
        with self.assertRaises(ValueError):
            self.s.mutate('new', dict(tracking_no=''))


if __name__ == '__main__':
    unittest.main(verbosity=2)
