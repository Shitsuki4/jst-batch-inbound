# -*- coding: utf-8 -*-
"""更换采购单号 / 外部单号并重新匹配：全程离线（不连聚水潭）。"""
import json
import unittest

from inbound_http_test import Fixture


def item(poi, sku, code, name, qty, in_qty=0):
    return {'poi_id': poi, 'sku_id': sku, 'sku_code': code, 'aux_codes': '', 'name': name, 'spec': '', 'qty': qty,
            'in_qty': in_qty, 'qty_known': True, 'in_qty_known': True, 'i_id': '', 'order_qty': qty}


NEW_ITEMS = [item('11', 'A', '690000000001', '测试商品 A', 5), item('13', 'C', '690000000003', '测试商品 C', 10)]


class RematchTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        srv = self.f.server
        self.calls = []
        self.po = {'456': ({'po_id': '456', 'outer_po_id': 'OUT-456', 'supplier': '新供应商', 'wh_name': '', 'status': '',
                            'po_date': ''}, {'owner': 'OWNER', 'auth': 'WAREHOUSE', 'id_type': 'po_id'}, NEW_ITEMS)}
        self.orig = (srv.find_purchase_order, srv.fetch_items)

        def find(no, owner='', auth='', prefer='', **kw):
            self.calls.append(no)
            for key, (row, ctx, _) in self.po.items():
                if no in (row['po_id'], row['outer_po_id']):
                    return dict(row), dict(ctx)
            raise srv.BizError('PO_NOT_FOUND', code='E-PO-1001', user_text='未找到采购单 %s' % no)

        def fetch(row, ctx):
            return json.loads(json.dumps(self.po[row['po_id']][2]))

        srv.find_purchase_order, srv.fetch_items = find, fetch

    def tearDown(self):
        self.f.server.find_purchase_order, self.f.server.fetch_items = self.orig
        self.f.close()

    def lines(self):
        return [dict(r) for r in self.f.server.STORE.db.execute(
            'SELECT id,batch_id,key,sku_id,qty,matched,poi_id FROM line WHERE session_id=? ORDER BY id', (self.f.sid,))]

    def preview(self, no='OUT-456', **extra):
        return self.f.call('/api/po/rematch/preview', dict({'sid': self.f.sid, 'no': no}, **extra))

    def test_preview_lists_matches_missing_and_over_without_writing(self):
        before = self.lines()
        r = self.preview()
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['old']['po_id'], '123')
        self.assertEqual(r['new']['po_id'], '456')
        self.assertEqual(r['new']['outer_po_id'], 'OUT-456')
        self.assertEqual([(m['sku_id'], m['received'], m['plan']) for m in r['matches']], [('A', 7, 5)])
        self.assertEqual([(m['sku_id'], m['over']) for m in r['over']], [('A', 2)])
        self.assertEqual([(m['sku_id'], m['received']) for m in r['missing']], [('B', 3)])
        self.assertEqual([m['sku_id'] for m in r['not_received']], ['C'])
        self.assertNotIn('_changes', r)
        self.assertEqual(self.lines(), before)
        self.assertEqual(self.f.server.STORE.get_session(self.f.sid)['po_id'], '123')

    def test_confirm_rematches_and_keeps_batches_quantities_and_audits(self):
        srv = self.f.server
        batches = srv.STORE.batches(self.f.sid)
        p = self.preview()
        r = self.f.call('/api/po/rematch/confirm', {'sid': self.f.sid, 'no': 'OUT-456', 'fingerprint': p['fingerprint'], 'confirmed': True})
        self.assertTrue(r['ok'], r)
        s = srv.STORE.get_session(self.f.sid)
        self.assertEqual((s['po_id'], s['outer_po_id'], s['input_no']), ('456', 'OUT-456', 'OUT-456'))
        self.assertEqual(json.loads(s['round_baseline_json'])[0]['sku_id'], 'A')
        self.assertEqual(srv.STORE.batches(self.f.sid), batches)
        lines = {l['sku_id']: l for l in self.lines()}
        self.assertEqual((lines['A']['key'], lines['A']['matched'], lines['A']['poi_id'], lines['A']['qty']), ('sku:A', 1, '11', 7))
        self.assertEqual((lines['B']['key'], lines['B']['matched'], lines['B']['poi_id'], lines['B']['qty']), ('sku:RAW:B', 0, '', 3))
        log = srv.STORE.db.execute('SELECT * FROM po_rematch_log WHERE sid=?', (self.f.sid,)).fetchall()
        self.assertEqual([(x['old_po'], x['old_outer'], x['new_po'], x['new_outer']) for x in log], [('123', '', '456', 'OUT-456')])
        ops = srv.OPLOG.query({'op': 'purchase_link'})
        rows = ops.get('rows') or ops.get('records') or ops.get('items') or []
        self.assertTrue(any('123' in (x.get('summary') or '') and '456' in (x.get('summary') or '') for x in rows), ops)
        # 再换回去：B 重新匹配回采购单，数量不变
        self.po['123'] = ({'po_id': '123', 'outer_po_id': 'OUT-123', 'supplier': '', 'wh_name': '', 'status': '', 'po_date': ''},
                          {'owner': 'OWNER', 'auth': 'WAREHOUSE', 'id_type': 'po_id'},
                          [item('1', 'A', '690000000001', '测试商品 A', 100), item('2', 'B', '690000000002', '测试商品 B', 200)])
        p = self.preview('123')
        self.assertEqual(p['missing'], [])
        r = self.f.call('/api/po/rematch/confirm', {'sid': self.f.sid, 'no': '123', 'fingerprint': p['fingerprint'], 'confirmed': True})
        self.assertTrue(r['ok'], r)
        lines = {l['sku_id']: l for l in self.lines()}
        self.assertEqual((lines['B']['key'], lines['B']['matched'], lines['B']['qty']), ('sku:B', 1, 3))

    def test_changed_data_after_preview_requires_new_preview(self):
        srv = self.f.server
        p = self.preview()
        bad = self.f.call('/api/po/rematch/confirm', {'sid': self.f.sid, 'no': 'OUT-456', 'fingerprint': 'x' * 64, 'confirmed': True})
        self.assertEqual(bad['error_code'], 'E-PO-2005')
        srv.STORE.add_qty(self.f.batch['id'], self.f.sid, {'sku_id': 'A', 'name': '测试商品 A', 'poi_id': '1'}, 1)
        r = self.f.call('/api/po/rematch/confirm', {'sid': self.f.sid, 'no': 'OUT-456', 'fingerprint': p['fingerprint'], 'confirmed': True})
        self.assertFalse(r['ok'])
        self.assertEqual(r['error_code'], 'E-PO-2005')
        self.assertTrue(r['msg'].startswith('[E-PO-2005]'))
        self.assertEqual(srv.STORE.get_session(self.f.sid)['po_id'], '123')
        no_confirm = self.f.call('/api/po/rematch/confirm', {'sid': self.f.sid, 'no': 'OUT-456', 'fingerprint': p['fingerprint']})
        self.assertEqual(no_confirm['error_code'], 'E-PO-2005')

    def test_blocks_when_inbound_generated(self):
        srv = self.f.server
        p = self.preview()
        srv.STORE.db.execute("INSERT INTO inbound_job(session_id,fingerprint,snapshot_json,login_co_id,status,created_at,updated_at)"
                             " VALUES(?,?,?,?,?,?,?)", (self.f.sid, 'fp-test', '{}', 'CO', 'rejected', 'now', 'now'))
        srv.STORE.db.commit()
        self.calls.clear()
        r = self.f.call('/api/po/rematch/confirm', {'sid': self.f.sid, 'no': 'OUT-456', 'fingerprint': p['fingerprint'], 'confirmed': True})
        self.assertEqual(r['error_code'], 'E-PO-2002')
        self.assertEqual(self.calls, [], '本地就能判断的不该去查聚水潭')
        self.assertEqual(srv.STORE.get_session(self.f.sid)['po_id'], '123')
        self.assertEqual(self.preview()['error_code'], 'E-PO-2002')

    def test_other_guards(self):
        srv = self.f.server
        self.po['123'] = ({'po_id': '123', 'outer_po_id': 'OUT-123', 'supplier': '', 'wh_name': '', 'status': '', 'po_date': ''},
                          {'owner': 'OWNER', 'auth': 'WAREHOUSE', 'id_type': 'po_id'}, NEW_ITEMS)
        self.assertEqual(self.preview('123')['error_code'], 'E-PO-2001')
        self.assertEqual(self.preview('')['error_code'], 'E-PO-2008')
        self.assertEqual(self.preview('NOPE')['error_code'], 'E-PO-1001')
        self.po['789'] = ({'po_id': '789', 'outer_po_id': '', 'supplier': '', 'wh_name': '', 'status': '', 'po_date': ''},
                          {'owner': 'OTHER', 'auth': 'WAREHOUSE', 'id_type': 'po_id'}, NEW_ITEMS)
        self.assertEqual(self.preview('789')['error_code'], 'E-PO-2004')
        other = srv.STORE.open_session('456', '456', 'po_id', {'owner_co_id': 'OWNER', 'auth_co_id': 'WAREHOUSE'}, NEW_ITEMS)
        self.assertEqual(self.preview()['error_code'], 'E-PO-2003')
        srv.STORE.db.execute("UPDATE session SET state='closed' WHERE id=?", (other,))
        srv.STORE.db.execute("UPDATE session SET state='closed' WHERE id=?", (self.f.sid,))
        srv.STORE.db.commit()
        self.assertEqual(self.preview()['error_code'], 'E-PO-2007')

    def test_unmatched_line_becomes_matched_and_duplicate_keys_merge(self):
        srv = self.f.server
        b = self.f.batch['id']
        srv.STORE.add_qty(b, self.f.sid, {'key': 'RAW:C', 'sku_id': 'C', 'sku_code': '690000000003', 'name': 'C', 'matched': False}, 4)
        srv.STORE.add_qty(b, self.f.sid, {'key': 'RAW:A', 'sku_id': 'A', 'sku_code': '690000000001', 'name': 'A', 'matched': False}, 2)
        p = self.preview()
        self.assertEqual(sorted((m['sku_id'], m['received']) for m in p['matches']), [('A', 9), ('C', 4)])
        r = self.f.call('/api/po/rematch/confirm', {'sid': self.f.sid, 'no': 'OUT-456', 'fingerprint': p['fingerprint'], 'confirmed': True})
        self.assertTrue(r['ok'], r)
        rows = [(l['key'], l['qty'], l['matched']) for l in self.lines()]
        self.assertIn(('sku:A', 9, 1), rows)
        self.assertIn(('sku:C', 4, 1), rows)
        self.assertEqual(sum(l['qty'] for l in self.lines()), 16)


if __name__ == '__main__':
    unittest.main()
