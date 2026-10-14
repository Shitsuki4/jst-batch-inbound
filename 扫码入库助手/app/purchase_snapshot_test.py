"""Isolated SQLite contract tests; never read a production database."""
import json, sqlite3, tempfile, unittest
from pathlib import Path
from store import Store
from purchase_snapshot import initial_view, live_view

HEAD = {'owner_co_id':'O','auth_co_id':'W'}
def item(sku='A', qty=12, name='原始商品'):
    return dict(sku_id=sku,name=name,qty=qty,in_qty=2,qty_known=True,in_qty_known=True,sku_code='6900')
class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=str(Path(self.tmp.name)/'test.db');self.st=Store(self.path)
        self.sid=self.st.open_session('PO','PO','po_id',HEAD,[item()]);self.bid=self.st.ensure_batch(self.sid,'物流A')[0]['id']
        self.lid=self.st.add_qty(self.bid,self.sid,dict(sku_id='A',name='实收名称',matched=True),4)[0]
    def tearDown(self):self.st.db.close();self.tmp.cleanup()
    def refresh(self, items):self.st.open_session('PO','PO','po_id',HEAD,items)
    def snap(self):return initial_view(self.st,self.sid)
    def row(self):return self.snap()['rows'][0]
    def reopen(self):self.st.db.close();self.st=Store(self.path)
    def legacy(self, baseline):
        self.st.db.executescript('DROP TRIGGER purchase_initial_no_update; DROP TRIGGER purchase_initial_no_delete; DROP TABLE purchase_initial;')
        self.st.db.execute('UPDATE session SET round_baseline_json=? WHERE id=?',(baseline,self.sid));self.st.db.commit();self.reopen()
    def test_first_snapshot_survives_changed_quantity_and_name(self):
        self.refresh([item(qty=20,name='新的名称')]);self.assertEqual((self.row()['plan'],self.row()['name']),(12,'原始商品'))
        self.assertEqual((live_view(self.st,self.sid)[0]['plan'],live_view(self.st,self.sid)[0]['name']),(20,'新的名称'))
    def test_round_difference_unchanged(self):
        before=self.st.receiving_round(self.sid);self.refresh([item(qty=20)])
        self.assertEqual(self.st.receiving_round(self.sid)['rows'][0]['plan'],before['rows'][0]['plan'])
        self.assertEqual(before['rows'][0]['plan'],10)
    def test_edit_qty_updates_both_not_initial_plan(self):
        self.st.set_qty(self.lid,7);self.assertEqual((self.row()['plan'],self.row()['recv'],self.row()['diff']),(12,7,-5))
        self.assertEqual(live_view(self.st,self.sid)[0]['recv'],7)
    def test_multiple_logistics_sum(self):
        bid=self.st.ensure_batch(self.sid,'物流B')[0]['id'];self.st.add_qty(bid,self.sid,dict(sku_id='A',matched=True),3)
        self.assertEqual(self.row()['recv'],7)
    def test_line_and_batch_deletion_update_receipts(self):
        self.st.del_line(self.lid);self.assertEqual(self.row()['recv'],0)
        self.st.add_qty(self.bid,self.sid,dict(sku_id='A',matched=True),2);self.st.del_batch(self.bid);self.assertEqual(self.row()['recv'],0)
    def test_added_removed_skus_only_change_live_purchase(self):
        self.refresh([item('B',5,'新增商品')]);self.st.add_qty(self.bid,self.sid,dict(sku_id='B',matched=True),2)
        rows=self.snap()['rows'];self.assertEqual([r['sku_id'] for r in rows],['A','B']);self.assertEqual(rows[1]['plan'],0);self.assertTrue(rows[1]['extra'])
        self.assertEqual(live_view(self.st,self.sid)[0]['sku_id'],'B')
    def test_same_po_new_round_shares_snapshot_not_receipts(self):
        self.st.close_session(self.sid);new=self.st.open_session('PO','PO','po_id',HEAD,[item(qty=30)])
        r=initial_view(self.st,new)['rows'][0];self.assertEqual((r['plan'],r['recv']),(12,0));self.assertEqual(self.row()['recv'],4)
    def test_company_scope_isolation(self):
        for head in ({'owner_co_id':'O2','auth_co_id':'W'},{'owner_co_id':'O','auth_co_id':'W2'}):
            sid=self.st.open_session('PO','PO','po_id',head,[item(qty=99)]);self.assertEqual(initial_view(self.st,sid)['rows'][0]['plan'],99)
        self.assertEqual(self.row()['plan'],12)
    def test_restart_does_not_rebase(self):
        self.refresh([item(qty=20)]);self.reopen();self.assertEqual(self.row()['plan'],12);self.assertEqual(self.snap()['source'],'first_query')
    def test_deleting_record_does_not_erase_first_purchase(self):
        self.st.del_session(self.sid);sid=self.st.open_session('PO','PO','po_id',HEAD,[item(qty=40)])
        self.assertEqual(initial_view(self.st,sid)['rows'][0]['plan'],12)
    def test_duplicate_skus_and_raw_keys_aggregate_exactly(self):
        self.refresh([item(qty=6),item(qty=8)]);self.st.add_qty(self.bid,self.sid,dict(key='sku:RAW:A',sku_id='A',matched=False),3)
        r=live_view(self.st,self.sid)[0];self.assertEqual((r['plan'],r['recv']),(14,7));self.assertEqual(self.row()['recv'],7)
    def test_unknown_quantity_is_not_zero(self):
        self.refresh([item(qty=None)]);r=live_view(self.st,self.sid)[0];self.assertIsNone(r['plan']);self.assertIsNone(r['diff'])
    def test_empty_first_snapshot_never_replaced(self):
        sid=self.st.open_session('E','E','po_id',HEAD,[]);self.st.open_session('E','E','po_id',HEAD,[item()])
        self.assertTrue(initial_view(self.st,sid)['available']);self.assertEqual(initial_view(self.st,sid)['rows'],[])
    def test_legacy_migration_uses_retained_baseline_not_latest(self):
        self.refresh([item(qty=80)]);self.legacy(json.dumps([item()]))
        self.assertEqual(self.row()['plan'],12);self.assertEqual(self.snap()['source'],'earliest_retained_round')
    def test_legacy_missing_baseline_stays_explicitly_unavailable(self):
        self.legacy(None);self.refresh([item(qty=80)]);self.reopen();self.assertFalse(self.snap()['available'])
    def test_legacy_invalid_baseline_not_guessed(self):
        self.legacy('{bad');self.assertFalse(self.snap()['available'])
    def test_database_rejects_overwriting_initial(self):
        with self.assertRaises(sqlite3.IntegrityError):self.st.db.execute("UPDATE purchase_initial SET items_json='[]'")
        self.st.db.rollback();self.assertEqual(self.row()['plan'],12)
if __name__=='__main__':unittest.main(verbosity=2)
