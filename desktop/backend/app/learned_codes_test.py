"""Offline unknown-barcode learning tests: isolated fixture DBs and FakeJst only."""
import os, tempfile, unittest
from catalog import scope_key
from learned_codes import LearnedCodes, take_notices, ACTIVE, SUPERSEDED, CONFLICT
from pda_test import Fixture

KEY = scope_key('1500', '1200', '1500')


class StoreUnitTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory(prefix='jst-learned-')
        self.db = LearnedCodes(os.path.join(self.d.name, 'learned.db'))
        take_notices()
    def tearDown(self): self.d.cleanup()

    def test_scope_isolation_between_companies(self):
        other = scope_key('9999', '1200', '1500')
        self.db.learn(KEY, 'UNK-1', {'sku_id': 'B', 'name': 'B'})
        self.assertEqual(self.db.active(KEY, 'unk-1')['sku_id'], 'B')
        self.assertIsNone(self.db.active(other, 'UNK-1'))
        self.assertIsNone(self.db.active(scope_key('1500', '7777', '1500'), 'UNK-1'))
        self.assertEqual(self.db.rows('9999'), [])
        self.assertEqual(len(self.db.rows('1500')), 1)
        rid = self.db.rows('1500')[0]['id']
        self.assertEqual(self.db.delete('9999', [rid]), 0)
        self.assertIsNone(self.db.reconcile(other, 'UNK-1', [{'sku_id': 'A'}]))
        self.assertEqual(self.db.active(KEY, 'UNK-1')['status'], ACTIVE)
        self.assertEqual(self.db.delete('1500', [rid]), 1)

    def test_reconcile_conflict_notice_once_and_superseded_silent(self):
        self.db.learn(KEY, 'C1', {'sku_id': 'B'})
        self.db.learn(KEY, 'S1', {'sku_id': 'B'})
        self.assertTrue(self.db.reconcile(KEY, 'C1', [{'sku_id': 'A'}]))
        self.assertIsNone(self.db.reconcile(KEY, 'C1', [{'sku_id': 'A'}]))
        self.assertEqual(len(take_notices()), 1)
        self.assertIsNone(self.db.reconcile(KEY, 'S1', [{'sku_id': 'B'}]))
        self.assertEqual(take_notices(), [])
        st = {r['code']: r['status'] for r in self.db.rows('1500')}
        self.assertEqual(st, {'C1': CONFLICT, 'S1': SUPERSEDED})


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture(); self.s = self.f.server
        self.s.LEARNED = LearnedCodes(os.path.join(self.f.temp.name, 'learned.db'))
        self.s.JST.coid = '1500'
        take_notices()
        self.catalog()
    def tearDown(self): self.f.close()

    def catalog(self, a_codes='', b_enabled=True):
        rows = [dict(sku_id='A', sku_code='690000000001', aux_codes=a_codes, name='测试商品A', enabled=True),
                dict(sku_id='B', sku_code='690000000002', aux_codes='', name='测试商品B', enabled=b_enabled)]
        self.s.CATALOG.replace(KEY, '测试货主', rows)
    def session(self): return self.s.STORE.get_session(self.f.sid)
    def cands(self, code): return self.s.receipt_candidates(self.session(), code)

    def test_learned_used_then_catalog_wins_with_single_notice(self):
        self.s.learn_code('purchase', self.f.sid, 'UNK-X', 'B')
        c = self.cands('UNK-X')
        self.assertEqual([x['sku_id'] for x in c], ['B']); self.assertEqual(c[0]['match_via'], '学习匹配')
        self.catalog(a_codes='UNK-X')
        c = self.cands('UNK-X')
        self.assertEqual([x['sku_id'] for x in c], ['A']); self.assertNotEqual(c[0].get('match_via'), '学习匹配')
        self.assertEqual(len(take_notices()), 1)
        self.assertEqual([x['sku_id'] for x in self.cands('UNK-X')], ['A'])
        self.assertEqual(take_notices(), [])
        self.assertEqual(self.s.LEARNED.rows('1500')[0]['status'], CONFLICT)

    def test_catalog_same_sku_marks_superseded(self):
        self.s.learn_code('purchase', self.f.sid, 'UNK-S', 'A')
        self.catalog(a_codes='UNK-S')
        self.assertEqual([x['sku_id'] for x in self.cands('UNK-S')], ['A'])
        self.assertEqual(take_notices(), [])
        self.assertEqual(self.s.LEARNED.rows('1500')[0]['status'], SUPERSEDED)

    def test_company_switch_does_not_use_other_scope(self):
        self.s.learn_code('purchase', self.f.sid, 'UNK-Y', 'B')
        self.s.JST.coid = '9999'
        self.assertEqual(self.s.learned_resolve('UNK-Y', '1200', '1500'), [])
        self.assertEqual(self.s.LEARNED.rows(self.s.learned_company()), [])

    def test_refused_when_code_already_resolves(self):
        with self.assertRaisesRegex(ValueError, '已有商品资料'):
            self.s.learn_code('purchase', self.f.sid, '690000000001', 'B')   # PO item barcode
        self.catalog(a_codes='CAT-ONLY')
        with self.assertRaisesRegex(ValueError, '已有商品资料'):
            self.s.learn_code('purchase', self.f.sid, 'CAT-ONLY', 'B')       # catalog aux code
        self.assertEqual(self.s.LEARNED.rows('1500'), [])

    def test_disabled_product_cannot_be_learned(self):
        self.catalog(b_enabled=False)
        with self.assertRaisesRegex(ValueError, '禁用'):
            self.s.learn_code('purchase', self.f.sid, 'UNK-D', 'B')
        self.assertEqual(self.s.LEARNED.rows('1500'), [])

    def check_learned_line(self, svc, sid, bid, rev, code, kind):
        r = svc.mutate('scan', dict(sid=sid, batch_id=bid, revision=rev(), code=code, sku_id='B', qty=1))
        lines = lambda d: [l for b in d['batches'] if b['id'] == bid for l in b['lines']]
        line = lines(r)[0]
        self.assertEqual((line['sku_id'], line.get('match_src')), ('B', 'learned'), kind)
        self.assertEqual(lines(svc.state(sid))[0]['match_src'], 'learned', kind)
        self.assertEqual(svc.store.db.execute('SELECT match_src FROM line WHERE id=?', (line['id'],)).fetchone()[0], 'learned', kind)
        return line

    def test_unplanned_and_joint_lines_persist_match_src(self):
        # 无采购单 / 联合收货：学习匹配识别的明细要落库并返回 match_src，前端才显示「学习匹配」标记
        from pathlib import Path
        from joint_receipt import JointService
        from joint_receipt_test import JointFake
        self.s.UNPLANNED.instance = None
        U = self.s.UNPLANNED
        sid = U.new('t', 'T0', owner='1200', auth='1500')['session']['id']
        rev = lambda: U.state(sid)['meta']['revision']
        bid = U.mutate('tracking', dict(sid=sid, revision=rev(), tracking_no='T1'))['current_batch']
        self.s.learn_code('unplanned', sid, 'UNK-U', 'B')
        line = self.check_learned_line(U, sid, bid, rev, 'UNK-U', 'unplanned')
        self.assertEqual([x['match_src'] for x in U.state(sid)['rows'] if x['sku_id'] == 'B'], ['learned'])
        # 改成别的商品后不再是学习匹配那一行，标记清除
        r = U.mutate('edit', dict(sid=sid, batch_id=bid, revision=rev(), line_id=line['id'], sku_id='A', name='测试商品A', sku_code='', spec='', qty=1))
        self.assertEqual([l['match_src'] for b in r['batches'] for l in b['lines'] if l['id'] == line['id']], [''])
        # 联合收货（同一 learned_resolve 兜底，商品资料范围 1200/1500）
        find = lambda no, **k: (dict(po_id=no, owner_co_id='OWNER', wms_co_id='WAREHOUSE'), dict(owner='OWNER', auth='WAREHOUSE'))
        J = JointService(Path(self.f.temp.name) / 'joint-learn.db', JointFake(), find, lambda *a, **k: [],
                         learned=lambda code, o, a, found: self.s.learned_resolve(code, '1200', '1500', found))
        try:
            jsid = J.new(dict(purchase_nos=['1', '2'], mother='123', confirmed=True))['session']['id']
            jrev = lambda: J.state(jsid)['meta']['revision']
            jbid = J.mutate('tracking', dict(sid=jsid, revision=jrev(), tracking_no='123-1', confirmed=True))['current_batch']
            self.s.LEARNED.learn(KEY, 'UNK-J', {'sku_id': 'B', 'name': '测试商品B'})
            self.check_learned_line(J, jsid, jbid, jrev, 'UNK-J', 'joint')
            self.assertEqual([r.get('match_src') for r in J.state(jsid)['rows'] if r['sku_id'] == 'B'], ['learned'])
        finally:
            J.store.db.close()

    def test_old_receipt_db_gets_match_src_column(self):
        import sqlite3
        from store import Store
        path = os.path.join(self.f.temp.name, 'old-unplanned.db')
        Store(path).db.close()
        db = sqlite3.connect(path); db.execute('ALTER TABLE line DROP COLUMN match_src'); db.commit(); db.close()
        st = Store(path)
        self.assertIn('match_src', {r[1] for r in st.db.execute('PRAGMA table_info(line)')})
        st.db.close()

    def test_pda_learn_only_purchase_receipts(self):
        self.f.pair()
        st, r, _ = self.f.http_call('/api/learned/learn', {'kind': 'unplanned', 'sid': self.f.sid, 'code': 'P1', 'sku_id': 'B'})
        self.assertFalse(r['ok'], r)
        st, r, _ = self.f.http_call('/api/learned/learn', {'kind': 'pda', 'sid': self.f.sid + 999, 'code': 'P1', 'sku_id': 'B'})
        self.assertFalse(r['ok'], r)
        st, r, _ = self.f.http_call('/api/learned/learn', {'kind': 'pda', 'sid': self.f.sid, 'code': 'P1', 'sku_id': 'B'})
        self.assertTrue(r['ok'], r)
        st, r, _ = self.f.http_call('/api/learned/search?kind=purchase&sid=%d&q=B' % self.f.sid)
        self.assertTrue(r['ok'], r); self.assertIn('B', [x['sku_id'] for x in r['rows']])
        st, r, _ = self.f.http_call('/api/learned/list')
        self.assertFalse(r['ok'])


if __name__ == '__main__': unittest.main(verbosity=2)
