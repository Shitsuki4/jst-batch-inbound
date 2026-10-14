"""2.0.18 per-operation mode + precise reservation; isolated FakeJst only."""
import json, sqlite3, unittest
from decimal import Decimal
from unittest.mock import patch
import inbound_test as base
from inbound import InboundError, InboundService
from store import Store

class ModeTests(unittest.TestCase):
    setUp=base.InboundTests.setUp
    tearDown=base.InboundTests.tearDown
    add=base.InboundTests.add
    actual=base.InboundTests.actual
    def mixed(self):
        self.a=self.add(self.b1,'A',2)
        self.x=self.add(self.b1,'X',5,False)
    def preview(self,mode='purchase_only'):
        return self.svc.preview(self.sid,'1',mode=mode)
    def create(self,mode='purchase_only',preview=None):
        p=preview or self.preview(mode)
        return self.svc.create(self.sid,p['fingerprint'],'1',mode=mode)
    def test_excluded_kept_and_later_all_only_sends_remaining(self):
        self.mixed();p=self.preview()
        self.assertEqual(p['total_qty'],'2.0');self.assertEqual(p['excluded_qty'],'5.0')
        self.assertEqual([x['sku_id'] for x in p['snapshot']['excluded_items']],['X'])
        j=self.create(preview=p);self.assertEqual(self.actual(j),{'A':Decimal(2)})
        lines=self.st.batch_lines(self.b1['id']);self.assertEqual(len(lines),2)
        self.assertEqual({x['sku_id']:bool(x['submitted_job_id']) for x in lines},{'A':True,'X':False})
        self.assertEqual(self.st.batches(self.sid)[0]['inbound_pending_qty'],5)
        p2=self.preview('all_received');self.assertEqual([x['sku_id'] for x in p2['snapshot']['items']],['X'])
        j2=self.create('all_received',p2);self.assertEqual(self.actual(j2),{'X':Decimal(5)})
        self.assertEqual(self.st.batches(self.sid)[0]['inbound_pending_qty'],0)
        self.assertEqual(self.preview('all_received')['snapshot']['items'],[])
        self.assertEqual(self.create(preview=p)['id'],j['id']);self.assertEqual(self.create('all_received',p2)['id'],j2['id'])
        self.assertEqual(self.jst.creates,2)
    def test_all_includes_outside_without_mutating_purchase(self):
        self.mixed();j=self.create('all_received');self.assertEqual(self.actual(j),{'A':Decimal(2),'X':Decimal(5)})
        self.assertFalse(json.loads(j['snapshot_json'])['validate_purchase_details'])
        self.assertEqual(self.jst.detail_rows,[{'sku_id':'A','qty':100},{'sku_id':'B','qty':200}])
        self.assertNotIn('ConfirmBatch',[c[1] for c in self.jst.calls])
    def test_choice_ignores_global_setting(self):
        self.mixed()
        for old in ['0','1']:
            self.st.set_kv('inbound_validate_purchase',old)
            self.assertEqual(self.preview()['total_qty'],'2.0')
            self.assertEqual(self.preview('all_received')['total_qty'],'7.0')
    def test_sku_membership_not_stale_matched_flag(self):
        self.add(self.b1,'A',2,False);self.add(self.b1,'X',5,True)
        self.assertEqual(self.actual(self.create()),{'A':Decimal(2)})
    def test_mode_fingerprint_tampering_and_invalid(self):
        self.mixed();p=self.preview()
        self.assertNotEqual(p['fingerprint'],self.preview('all_received')['fingerprint'])
        with self.assertRaisesRegex(InboundError,'变化'):self.create('all_received',p)
        for mode in ['',False,{},'anything']:
            with self.assertRaises(InboundError):self.preview(mode)
        self.assertEqual(self.jst.creates,0)
    def test_existing_job_cannot_change_mode(self):
        self.mixed();p=self.preview();self.create(preview=p)
        with self.assertRaisesRegex(InboundError,'方式'):self.create('all_received',p)
        self.assertEqual(self.jst.creates,1)
    def test_only_outside_creates_nothing(self):
        self.add(self.b1,'X',7,False);p=self.preview()
        self.assertEqual(p['excluded_qty'],'7.0');self.assertEqual(p['snapshot']['items'],[])
        with self.assertRaises(InboundError):self.create(preview=p)
        self.assertFalse(self.st.inbound_jobs(self.sid));self.assertEqual(self.jst.creates,0)
    def test_excluded_change_invalidates_preview(self):
        self.mixed();p=self.preview();self.st.set_qty(self.x,6)
        with self.assertRaisesRegex(InboundError,'变化'):self.create(preview=p)
        self.assertEqual(self.jst.creates,0)
    def test_missing_product_blocks_all_before_reserve(self):
        self.mixed();self.jst.products.pop('X')
        with self.assertRaises(InboundError):self.create('all_received')
        self.assertFalse(self.st.inbound_jobs(self.sid));self.assertEqual(self.jst.creates,0)
        self.create();self.assertEqual(self.jst.creates,1)
    def test_excluded_no_longer_editable_but_not_submitted(self):
        self.mixed();self.create()
        with self.assertRaises(Exception):self.st.set_qty(self.x,9)
        with self.assertRaises(sqlite3.IntegrityError):self.st.db.execute('DELETE FROM line WHERE id=?',(self.x,))
        self.st.db.rollback();self.assertEqual(self.preview('all_received')['total_qty'],'5.0')
    def test_restart_persists_remaining(self):
        self.mixed();self.create();self.st.db.close();self.st=Store(self.path);self.svc=InboundService(self.st,self.jst)
        self.assertEqual(self.preview('all_received')['total_qty'],'5.0');self.create('all_received');self.assertEqual(self.jst.creates,2)
    def test_legacy_whole_batch_lock_retained(self):
        self.mixed();self.create('all_received')
        self.st.db.execute('DELETE FROM inbound_line');self.st.db.execute('UPDATE inbound_batch SET line_scoped=0');self.st.db.commit()
        self.assertEqual(self.preview('all_received')['snapshot']['items'],[])
        self.assertEqual(self.st.batches(self.sid)[0]['inbound_pending_qty'],0)
    def test_old_schema_migrates_conservatively(self):
        self.st.db.execute('ALTER TABLE inbound_batch DROP COLUMN line_scoped');self.st.db.close()
        self.st=Store(self.path);self.assertIn('line_scoped',{r['name'] for r in self.st.db.execute('PRAGMA table_info(inbound_batch)')})
    def test_rejected_second_releases_only_its_lines(self):
        from inbound import CallbackRejected
        self.mixed();first=self.create();original=self.jst.callback
        def reject(url,method,args,**kw):
            if method=='CreatePurchaseIn':raise CallbackRejected('test reject')
            return original(url,method,args,**kw)
        # API boundary is mocked directly, while the real store reservation path still executes.
        from inbound import PurchaseInAPI
        with patch.object(PurchaseInAPI,'create',side_effect=CallbackRejected('test reject')):
            with self.assertRaisesRegex(InboundError,'明确拒绝'):self.create('all_received')
        self.assertEqual(self.st.batches(self.sid)[0]['inbound_job_id'],first['id'])
        self.assertEqual(self.preview('all_received')['total_qty'],'5.0')
        self.create('all_received');self.assertEqual(self.jst.creates,2)
    def test_pending_first_blocks_other_mode(self):
        self.mixed();self.jst.fail_qty=True
        with self.assertRaises(InboundError):self.create()
        with self.assertRaisesRegex(InboundError,'未完成'):self.create('all_received')
        self.assertEqual(self.jst.creates,1)
        self.jst.fail_qty=False;self.svc.resume(self.st.inbound_jobs(self.sid)[0]['id']);self.create('all_received')
        self.assertEqual(self.jst.creates,2)
    def test_unknown_first_blocks_other_mode(self):
        self.mixed();self.jst.lose_create=True
        with self.assertRaisesRegex(InboundError,'待核实'):self.create()
        with self.assertRaisesRegex(InboundError,'未完成'):self.create('all_received')
        self.assertEqual(self.jst.creates,1)
    def test_overlapping_snapshots_fail_reservation(self):
        self.mixed();p=self.preview();q=self.preview('all_received');self.st.reserve_inbound(p['snapshot'],p['fingerprint'],self.jst.coid)
        with self.assertRaises(sqlite3.IntegrityError):self.st.reserve_inbound(q['snapshot'],q['fingerprint'],self.jst.coid)
        self.assertEqual(len(self.st.inbound_jobs(self.sid)),1)
    def test_purchase_changed_before_write_stops(self):
        self.mixed();p=self.preview();self.jst.detail_rows=[]
        with self.assertRaises(InboundError):self.create(preview=p)
        self.assertEqual(self.jst.creates,0)
    def test_multiple_batches_same_sku_partial(self):
        self.mixed();self.add(self.b2,'A',3);self.add(self.b2,'X',4,False)
        self.assertEqual(self.actual(self.create()),{'A':Decimal(5)})
        self.assertEqual(self.actual(self.create('all_received')),{'X':Decimal(9)})

if __name__=='__main__':unittest.main()
