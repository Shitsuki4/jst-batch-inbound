# -*- coding: utf-8 -*-
"""unplanned_hint：普通收货扫物流单号时，只读查询「无采购单收货」登记。"""
import os
import sqlite3
import tempfile
import unittest

import unplanned_hint
from inbound_test import FakeJst
from unplanned import UnplannedService


class UnplannedHintTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'unplanned.db')
        self.j = FakeJst()
        self.j.detail_rows = [dict(sku_id='A', qty=5, in_qty=0, poi_id='1', name='商品A')]
        self.s = UnplannedService(self.path, self.j,
                                  lambda no, **scope: ({'po_id': no}, {'owner': 'OWNER', 'auth': 'WAREHOUSE'}),
                                  lambda *a, **k: [])

    def tearDown(self):
        self.s.store.db.close()
        self.tmp.cleanup()

    def register(self, tracking, items=(('A', 3), ('B', 2)), note='到货无单'):
        st = self.s.new(note, tracking, owner='OWNER', auth='WAREHOUSE')
        sid, bid = st['session']['id'], st['current_batch']
        for sku, n in items:
            rev = self.s.state(sid)['meta']['revision']
            self.s.mutate('manual', dict(sid=sid, batch_id=bid, revision=rev, sku_id=sku, name='商品' + sku,
                                         sku_code='', spec='', qty=n))
        return sid, bid

    def test_missing_db_and_blank_tracking_return_none(self):
        self.assertIsNone(unplanned_hint.lookup(os.path.join(self.tmp.name, 'nope.db'), 'SF1', 'PO1'))
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, 'nope.db')), '不应创建库文件')
        self.assertIsNone(unplanned_hint.lookup(self.path, '   ', 'PO1'))

    def test_unlinked_registration_found_with_normalized_match(self):
        sid, _ = self.register('SF100 200')
        hint = unplanned_hint.lookup(self.path, '  sf100200 ', 'PO-9')
        self.assertIsNotNone(hint)
        self.assertEqual(hint['kind'], 'unlinked')
        rec = hint['record']
        self.assertEqual(rec['sid'], sid)
        self.assertEqual(rec['qty'], 5)
        self.assertEqual(rec['sku_count'], 2)
        self.assertTrue(rec['registered_at'])
        self.assertIn('尚未绑定采购单', hint['message'])
        self.assertIn('建议到无采购单收货中绑定本采购单', hint['message'])
        self.assertIn('5 件 / 2 个SKU', hint['message'])
        self.assertEqual(hint['open_url'], '/unplanned.html?sid=%d' % sid)

    def test_partial_or_different_number_not_matched(self):
        self.register('SF100200')
        self.assertIsNone(unplanned_hint.lookup(self.path, 'SF10020', 'PO-9'))
        self.assertIsNone(unplanned_hint.lookup(self.path, 'SF1002001', 'PO-9'))

    def test_linked_to_same_po_is_info_and_other_po_ignored(self):
        sid, _ = self.register('YT1')
        rev = self.s.state(sid)['meta']['revision']
        self.s.mutate('match', dict(sid=sid, revision=rev, po_id='PO-7'))
        self.assertEqual(self.s.session(sid)['po_id'], 'PO-7')
        same = unplanned_hint.lookup(self.path, 'YT1', 'PO-7')
        self.assertEqual(same['kind'], 'same_po')
        self.assertIn('已绑定本采购单，请勿重复收货', same['message'])
        self.assertIsNone(unplanned_hint.lookup(self.path, 'YT1', 'PO-8'))

    def test_submitted_unlinked_record_is_ignored(self):
        sid, _ = self.register('ZT1')
        with self.s.store.db:
            self.s.store.db.execute("INSERT INTO inbound_job(session_id,status,fingerprint,snapshot_json,login_co_id,created_at,updated_at) "
                                    "VALUES(?,?,?,?,?,?,?)", (sid, "unknown", "fp", "{}", "WAREHOUSE", "now", "now"))
        self.assertIsNone(unplanned_hint.lookup(self.path, 'ZT1', 'PO-1'))

    def test_closed_unlinked_record_still_reported(self):
        sid, _ = self.register('JD1')
        self.s.store.db.execute('UPDATE session SET state="closed" WHERE id=?', (sid,))
        self.s.store.db.commit()
        hint = unplanned_hint.lookup(self.path, 'JD1', 'PO-1')
        self.assertEqual(hint['record']['status'], '已结单，未绑定采购单')

    def test_lookup_is_read_only(self):
        self.register('RO1')
        before = self.s.store.db.execute('SELECT revision FROM unplanned_meta').fetchall()
        changes = self.s.store.db.total_changes
        unplanned_hint.lookup(self.path, 'RO1', '')
        # 只读连接不能写；验证 query_only 兜底路径同样拒绝写入。
        db = sqlite3.connect(self.path)
        db.execute('PRAGMA query_only=ON')
        with self.assertRaises(sqlite3.OperationalError):
            db.execute('DELETE FROM batch')
        db.close()
        self.assertEqual(before, self.s.store.db.execute('SELECT revision FROM unplanned_meta').fetchall())
        self.assertEqual(changes, self.s.store.db.total_changes)

    def test_foreign_or_corrupt_db(self):
        other = os.path.join(self.tmp.name, 'empty.db')
        sqlite3.connect(other).close()
        self.assertIsNone(unplanned_hint.lookup(other, 'SF1', ''))
        bad = os.path.join(self.tmp.name, 'bad.db')
        with open(bad, 'wb') as f:
            f.write(b'not a sqlite database' * 100)
        with self.assertRaises(sqlite3.DatabaseError):
            unplanned_hint.lookup(bad, 'SF1', '')


if __name__ == '__main__':
    unittest.main(verbosity=2)
