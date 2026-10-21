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
