"""Offline only: isolated DBs + protocol fake. Never opens real ERP or user DBs."""
import copy, json, os, tempfile, threading, unittest
from unittest.mock import patch
from pathlib import Path
from inbound_test import FakeJst
from unplanned import UnplannedService
from store import Store
from jst import normalize_po_item
from purchase_watch import PurchaseWatch, modified
from inbound import InboundError

class WatchFake(FakeJst):
    def __init__(self):
        super().__init__();self.cookies={};self.po_status='未完成';self.po_modified='2026-10-03 10:00:00';self.search_error=False
        self.detail_rows=[dict(sku_id='A',qty=10,in_qty=0,poi_id='11',name='粉色洗发水470ml',price=10)]
    def search_po(self,po,owner,auth,kind):
        if self.search_error:raise TimeoutError('模拟采购读取失败')
        return [dict(po_id=po,owner_co_id=owner,wms_co_id=auth,status=self.po_status,modified=self.po_modified)],1

class PurchaseWatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='purchase214-unit-');self.j=WatchFake();self.lock=threading.RLock()
        self.u=UnplannedService(Path(self.tmp.name)/'unplanned.db',self.j,lambda no:({'po_id':no},{'owner':'OWNER','auth':'WAREHOUSE'}),lambda *a,**k:[])
        self.orig=Store(Path(self.tmp.name)/'jst.db');self.stores={'receipt':self.orig,'unplanned':self.u.store}
        self.w=PurchaseWatch(Path(self.tmp.name)/'watch.db',self.j,lambda:self.stores,self.lock)
        self.sid=self.u.new('')['session']['id'];self.bid=self.change('tracking',tracking_no='T1')['current_batch'];self.change('manual',sku_id='A',name='粉色洗发水470ml',qty=8)
        self.change('match',po_id='123');self.wid=self.w.register('unplanned',self.sid)['id'];self.w.poll()
    def change(self,action,**kw):return self.u.mutate(action,dict(sid=self.sid,batch_id=getattr(self,'bid',0),revision=self.u.state(self.sid)['meta']['revision'],**kw))
    def get(self):return self.w.get(self.wid)
    def tearDown(self):self.w.db.close();self.u.store.db.close();self.orig.db.close();self.tmp.cleanup()
    def make_modified(self):self.j.po_modified='2026-10-03 10:01:00';self.w.poll();self.assertEqual(self.get()['state'],'modified')
    def service(self):return self.w.inbound(self.wid,'unplanned',self.sid)
    def create(self):
        s=self.service();p=s.preview(self.sid,'3');return s.create(self.sid,p['fingerprint'],'3')
    def confirm(self,job):
        self.j.docs[job['io_id']]['head']['status']='已入库'
        for p in self.j.detail_rows:p['in_qty']=sum(float(r['qty']) for d in self.j.docs.values() if d['head']['status']=='已入库' for r in d['rows'] if r['sku_id']==p['sku_id'])
    def test_first_read_baseline_and_timestamp(self):
        w=self.get();self.assertEqual(w['state'],'pending');self.assertEqual(w['modified'],self.j.po_modified);self.assertFalse(w['error']);self.assertEqual(modified({'modified':'2026-10-03'}),'')
    def test_modified_persists_then_matching_priority(self):
        self.make_modified();self.w.poll();self.assertEqual(self.get()['state'],'modified');self.assertEqual(self.w.listing()['yellow'],1)
        self.j.detail_rows[0]['qty']=8;self.w.poll();self.assertEqual(self.get()['state'],'matched');self.assertEqual((self.w.listing()['yellow'],self.w.listing()['red']),(0,1))
    def test_missing_time_never_fabricates_modification(self):
        self.j.po_modified='';self.j.detail_rows[0]['qty']=9;self.w.poll();self.assertEqual(self.get()['state'],'pending');self.assertTrue(self.get()['time_warning'])
    def test_all_six_manual_transitions_sticky_and_honest(self):
        for old in ('pending','modified','matched'):
            for new in ('pending','modified','matched'):
                if old==new:continue
                self.w.transition(self.wid,old);self.w.transition(self.wid,new,'test');self.w.poll();self.assertEqual(self.get()['state'],new);self.assertFalse(self.get()['actual_match'])
        self.assertGreater(self.w.db.execute('SELECT COUNT(*) FROM audit').fetchone()[0],10)
    def test_manual_pending_retains_notice(self):
        self.make_modified();self.w.transition(self.wid,'pending');self.w.poll();self.assertEqual(self.w.listing()['yellow'],1)
    def test_new_purchase_version_releases_manual_override(self):
        self.w.transition(self.wid,'matched');self.j.po_modified='2026-10-03 10:02:00';self.w.poll();self.assertEqual(self.get()['state'],'modified');self.assertIsNone(self.get()['manual'])
    def test_receipt_change_resets_automatic_state(self):
        self.make_modified();self.change('manual',sku_id='A',name='粉色洗发水470ml',qty=1);self.w.poll();self.assertEqual(self.get()['state'],'pending');self.assertEqual(self.w.listing()['yellow'],1)
    def test_failure_retains_state_and_notice(self):
        self.make_modified();self.j.search_error=True;self.w.poll();self.assertEqual(self.get()['state'],'modified');self.assertTrue(self.get()['error']);self.assertEqual(self.w.listing()['yellow'],1)
    def test_restart_persists_state_manual_and_notice(self):
        self.make_modified();self.w.transition(self.wid,'pending');self.w.db.close();self.w=PurchaseWatch(Path(self.tmp.name)/'watch.db',self.j,lambda:self.stores,self.lock);self.w.poll();self.assertEqual(self.get()['state'],'pending');self.assertEqual(self.w.listing()['yellow'],1)
    def test_duplicate_registration_is_one_identity(self):
        self.w.register('unplanned',self.sid);self.w.register('unplanned',self.sid);self.assertEqual(len(self.w.listing()['rows']),1)
    def test_two_completion_conditions_and_draft_does_not_clear(self):
        self.make_modified();job=self.create();self.assertEqual(job['status'],'complete');self.w.poll();self.assertFalse(self.get()['complete']);self.assertFalse(self.get()['inbound_complete']);self.assertEqual(self.w.listing()['yellow'],1)
        self.j.po_status='完成';self.w.poll();self.assertFalse(self.get()['complete']);self.confirm(job);self.w.poll();self.assertTrue(self.get()['complete']);self.assertEqual(self.w.listing()['yellow'],0)
    def test_inbound_only_not_enough_to_clear(self):
        self.make_modified();job=self.create();self.confirm(job);self.w.poll();self.assertTrue(self.get()['inbound_complete']);self.assertFalse(self.get()['complete']);self.assertEqual(self.w.listing()['yellow'],1)
    def test_difference_draft_actual_only_and_price_not_written(self):
        self.change('manual',sku_id='X',name='蓝色洗发水470ml',qty=2);self.w.poll();self.make_modified();p=self.service().preview(self.sid,'3');self.assertTrue(p['requires_difference_confirmation']);before=self.u.store.get_kv('inbound_validate_purchase','1');job=self.service().create(self.sid,p['fingerprint'],'3')
        self.assertEqual(self.u.store.get_kv('inbound_validate_purchase','1'),before);rows=self.j.docs[job['io_id']]['rows'];self.assertEqual({r['sku_id']:float(r['qty']) for r in rows},{'A':8,'X':2})
        methods=[c[1] for c in self.j.calls];self.assertIn('AddItemsUnify',methods);self.assertIn('AddItems',methods);self.assertFalse(any(m in ('Confirm','UpdatePrice','UpdateCost') for m in methods))
        for _,method,args,_ in self.j.calls:
            if method=='AddItemsUnify':self.assertTrue(all(set(p)=={'sku_id','in_qty','qty'} for p in json.loads(args[1])))
    def test_snapshot_change_rejects_before_erp_create(self):
        self.make_modified();s=self.service();p=s.preview(self.sid,'3');self.j.detail_rows[0]['price']=11
        with self.assertRaises(InboundError):s.create(self.sid,p['fingerprint'],'3')
        self.assertEqual(self.j.creates,0)
    def test_duplicate_click_and_unknown_result_continue_original(self):
        self.make_modified();s=self.service();p=s.preview(self.sid,'3');self.j.lose_create=True
        with self.assertRaises(InboundError):s.create(self.sid,p['fingerprint'],'3')
        with self.assertRaises(InboundError):s.create(self.sid,p['fingerprint'],'3')
        self.assertEqual(self.j.creates,1);self.j.lose_create=False
        job=self.u.store.inbound_jobs(self.sid)[0];s.resume(job['id'],'8001');s.create(self.sid,p['fingerprint'],'3');self.assertEqual(self.j.creates,1)
    def test_nonexistent_and_duplicate_sku_block_before_create(self):
        self.change('manual',sku_id='BAD',name='未知',qty=1);self.w.poll();self.make_modified()
        with self.assertRaises(InboundError):self.create()
        self.assertEqual(self.j.creates,0)
        self.j.detail_rows.append(dict(self.j.detail_rows[0]));self.w.poll()
        with self.assertRaises(InboundError):self.create()
    def test_cross_source_duplicate_guard(self):
        self.make_modified();self.create();items=[normalize_po_item(x) for x in self.j.detail_rows]
        sid=self.orig.open_session('123','123','po_id',dict(owner_co_id='OWNER',auth_co_id='WAREHOUSE'),items)
        with self.assertRaises(InboundError):self.w.cross_guard(self.orig,sid)
    def test_account_change_discards_late_read(self):
        old=self.get()['checked_at'];read=self.w.read
        def race(w,c,j):
            r=read(w,c,j);self.j.uid='OTHER';return r
        with patch.object(self.w,'read',race):self.w.poll_one(self.wid)
        self.j.uid='USER';self.assertEqual(self.get()['checked_at'],old)
    def test_erp_wrong_scope_preserves_state(self):
        self.make_modified()
        with patch.object(self.j,'search_po',return_value=([dict(po_id='123',owner_co_id='OTHER',wms_co_id='WAREHOUSE')],1)):self.w.poll()
        self.assertEqual(self.get()['state'],'modified');self.assertIn('公司',self.get()['error'])
    def test_multiple_receipt_rounds_are_counted_once_and_baseline_immutable(self):
        self.u.store.db.execute('UPDATE session SET po_id="other" WHERE id=?',(self.sid,));self.u.store.db.commit()
        head=dict(owner_co_id='OWNER',auth_co_id='WAREHOUSE')
        items=[normalize_po_item(dict(sku_id='A',qty=10,in_qty=0))]
        first=self.orig.open_session('123','123','po_id',head,items)
        b=self.orig.ensure_batch(first,'ROUND1')[0]['id'];self.orig.add_qty(b,first,dict(sku_id='A',matched=1),3)
        baseline=self.orig.get_session(first)['round_baseline_json'];self.orig.close_session(first)
        second=self.orig.open_session('123','123','po_id',head,items)
        b=self.orig.ensure_batch(second,'ROUND2')[0]['id'];self.orig.add_qty(b,second,dict(sku_id='A',matched=1),5)
        self.w.poll();self.assertEqual(self.get()['rows'][0]['recv'],8)
        self.j.detail_rows[0]['qty']=8;self.j.po_modified='2026-10-03 12:10:01';self.w.poll()
        self.assertTrue(self.get()['actual_match']);self.assertEqual(self.orig.get_session(first)['round_baseline_json'],baseline)
    def test_duplicate_tracking_across_sources_is_not_assumed_safe(self):
        head=dict(owner_co_id='OWNER',auth_co_id='WAREHOUSE')
        sid=self.orig.open_session('123','123','po_id',head,[normalize_po_item(r) for r in self.j.detail_rows])
        self.orig.ensure_batch(sid,'T1');self.w.poll();self.assertTrue(any('重复物流单' in x for x in self.get()['problems']))
    def test_background_wait_is_sixty_seconds_without_page(self):
        class Stop:
            def __init__(self):self.done=False;self.values=[];self.event=threading.Event()
            def is_set(self):return self.done
            def wait(self,n):self.values.append(n);self.done=True;self.event.set()
        stop=Stop();self.w.stop=stop
        with patch.object(self.w,'poll') as polling:
            self.w.start();self.assertTrue(stop.event.wait(2));polling.assert_called_once()
        self.assertEqual(stop.values,[60])
    def test_failed_confirmation_read_does_not_clear_alert(self):
        self.make_modified();job=self.create();self.confirm(job);self.j.po_status='完成'
        self.j.docs[job['io_id']]['rows'][0]['qty']='7';self.w.poll()
        self.assertFalse(self.get()['complete']);self.assertTrue(self.get()['error']);self.assertEqual(self.w.listing()['yellow'],1)
    def test_inventory_counter_and_row_token_do_not_cancel_manual_override(self):
        self.make_modified();job=self.create();self.w.transition(self.wid,'pending')
        self.confirm(job);self.j.detail_rows[0]['__KeyData']='new-ui-token';self.w.poll()
        self.assertEqual(self.get()['state'],'pending');self.assertIsNotNone(self.get()['manual'])
    def test_warehouse_change_invalidates_confirmation(self):
        self.make_modified();p=self.service().preview(self.sid,'3')
        with self.assertRaises(InboundError):self.service().create(self.sid,p['fingerprint'],'4')
        self.assertEqual(self.j.creates,0)
    def test_foreign_watch_cannot_continue_original_fingerprint(self):
        self.make_modified();self.create();self.j.uid='OTHER'
        with self.assertRaises(ValueError):self.service()
    def test_legacy_scope_is_unknown_not_guessed(self):
        self.u.store.db.execute('UPDATE session SET po_id="different" WHERE id=?',(self.sid,));self.u.store.db.commit()
        sid=self.orig.open_session('123','123','po_id',dict(owner_co_id='OWNER',auth_co_id='WAREHOUSE'),[normalize_po_item(r) for r in self.j.detail_rows]);self.orig.db.execute('UPDATE session SET round_baseline_json=NULL WHERE id=?',(sid,));self.orig.db.commit();self.w.poll();self.assertTrue(any('基线' in x for x in self.get()['problems']))

if __name__=='__main__':unittest.main(verbosity=2)
