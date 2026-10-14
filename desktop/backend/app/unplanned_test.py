import json, os, tempfile, unittest
from unittest.mock import patch
from unplanned import UnplannedService
from inbound import InboundError, PurchaseInAPI
from inbound_test import FakeJst
from jst import CallbackRejected

class UnplannedTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.j=FakeJst()
        self.j.detail_rows=[dict(sku_id='A',qty=12,in_qty=0,poi_id='11',name='商品A',price=19.5),dict(sku_id='B',qty=8,in_qty=0,poi_id='12',name='商品B',price=25)]
        self.s=UnplannedService(os.path.join(self.tmp.name,'unplanned.db'),self.j,lambda no,**scope:({'po_id':no},{'owner':'OWNER','auth':'WAREHOUSE'}),lambda *a,**k:[])
        self.sid=self.s.new('测试',owner='OWNER',auth='WAREHOUSE')['session']['id'];self.bid=0
    def tearDown(self): self.s.store.db.close();self.tmp.cleanup()
    def change(self,a,**kw):
        return self.s.mutate(a,dict(sid=self.sid,batch_id=self.bid,revision=self.s.state(self.sid)['meta']['revision'],**kw))
    def batch(self,no): self.bid=self.change('tracking',tracking_no=no)['current_batch'];return self.bid
    def add(self,sku,n): return self.change('manual',sku_id=sku,name='商品'+sku,sku_code='',spec='规格',qty=n)
    def receive(self):
        self.batch('SF001');self.add('A',5);self.add('B',8)
        self.batch('SF002');self.add('A',7)
    def match(self): return self.change('match',po_id='123')
    def create(self):
        p=self.s.inbound.preview(self.sid,'3');return self.s.inbound.create(self.sid,p['fingerprint'],'3')
    def test_no_po_actual_only_and_all_tracking(self):
        self.receive();s=self.s.state(self.sid)
        self.assertFalse(s['linked']);self.assertEqual(s['total'],20);self.assertEqual(len(s['batches']),2)
        self.assertTrue(all(r['plan'] is None and r['kind']=='neutral' for r in s['rows']))
        self.assertFalse(s['can_create']);self.assertIn('SF001',self.s.export(self.sid).decode('utf-8-sig'))
    def test_repeated_tracking_and_sku_accumulate(self):
        self.batch('T');self.add('A',2);self.add('A',3);self.batch('T')
        s=self.s.state(self.sid);self.assertEqual(len(s['batches']),1);self.assertEqual(s['total'],5)
    def test_edit_and_rename_after_link_invalidate(self):
        self.receive();self.assertTrue(self.match()['can_create'])
        r=self.s.state(self.sid)['batches'][-1]['lines'][0]
        s=self.change('edit',line_id=r['id'],sku_id='A',name='新名称',sku_code='',spec='',qty=6)
        self.assertFalse(s['checked']);self.assertFalse(s['can_create'])
        self.assertEqual(self.change('refresh')['rows'][0]['kind'],'diff')
        self.change('rename',tracking_no='NEW');self.assertEqual(self.s.history('NEW')[0]['id'],self.sid)
    def test_diff_both_directions_equal_total_not_pass(self):
        self.receive();self.j.detail_rows=[dict(sku_id='A',qty=13,in_qty=0),dict(sku_id='X',qty=7,in_qty=0)]
        s=self.match();self.assertEqual(sum(r['plan'] for r in s['rows']),s['total'])
        self.assertEqual([r['kind'] for r in s['rows']],['diff','diff','extra']);self.assertFalse(s['can_create'])
        with self.assertRaises(InboundError):self.create()
        self.assertEqual(self.j.creates,0)
    def test_duplicate_sku_and_unknown_counter_block(self):
        self.receive();self.j.detail_rows.append(dict(self.j.detail_rows[0]));self.assertFalse(self.match()['can_create'])
        self.j.detail_rows=self.j.detail_rows[:2];del self.j.detail_rows[0]['in_qty'];self.assertFalse(self.match()['can_create'])
    def test_existing_erp_receipt_blocks(self):
        self.receive();self.j.detail_rows[0]['in_qty']=1;self.assertFalse(self.match()['can_create'])
    def test_all_receipts_one_draft_idempotent_and_prices_untouched(self):
        self.receive();self.match();p=self.s.inbound.preview(self.sid,'3')
        job=self.s.inbound.create(self.sid,p['fingerprint'],'3');self.assertEqual(job['status'],'complete')
        self.assertEqual(self.j.docs[job['io_id']]['head']['status'],'待入库')
        self.assertEqual({r['sku_id']:float(r['qty']) for r in self.j.docs[job['io_id']]['rows']},{'A':12,'B':8})
        self.s.inbound.create(self.sid,p['fingerprint'],'3');self.assertEqual(self.j.creates,1)
        for _,method,args,_ in self.j.calls:
            self.assertNotIn(method,['AddItems','Confirm','UpdatePrice','UpdateCost'])
            if method=='AddItemsUnify':self.assertTrue(all(set(p)=={'sku_id','in_qty','qty'} for p in json.loads(args[1])))
        self.assertTrue(self.s.state(self.sid)['locked'])
        for a,kw in [('tracking',dict(tracking_no='NEW')),('rename',dict(tracking_no='NEW')),('manual',dict(sku_id='A',name='A',qty=1)),('match',dict(po_id='456'))]:
            with self.assertRaises(ValueError):self.change(a,**kw)
    def test_changes_after_preview_block_creation(self):
        self.receive();self.match();p=self.s.inbound.preview(self.sid,'3')
        self.j.detail_rows[0]['price']=99
        with self.assertRaisesRegex(InboundError,'变化'):self.s.inbound.create(self.sid,p['fingerprint'],'3')
        self.assertEqual(self.j.creates,0)
        p=self.s.inbound.preview(self.sid,'3');self.j.detail_rows[0]['qty']=13
        with self.assertRaises(InboundError):self.s.inbound.create(self.sid,p['fingerprint'],'3')
        self.assertEqual(self.j.creates,0)
    def test_timeout_locks_whole_session_and_resume(self):
        self.receive();self.match();p=self.s.inbound.preview(self.sid,'3');self.j.lose_create=True
        with self.assertRaisesRegex(InboundError,'结果待核实'):self.s.inbound.create(self.sid,p['fingerprint'],'3')
        with self.assertRaises(InboundError):self.s.inbound.create(self.sid,p['fingerprint'],'3')
        with self.assertRaises(ValueError):self.batch('NO')
        job=self.s.store.inbound_jobs(self.sid)[0];self.s.inbound.resume(job['id'],'8001');self.assertEqual(self.j.creates,1)
    def test_rejected_request_unlocks(self):
        self.receive();self.match()
        with patch.object(PurchaseInAPI,'create',side_effect=CallbackRejected('拒绝')):
            with self.assertRaises(InboundError):self.create()
        self.assertFalse(self.s.state(self.sid)['locked']);self.batch('NEW')
    def test_stale_revision_foreign_ids_bad_quantity(self):
        self.batch('T');self.add('A',1)
        with self.assertRaises(ValueError):self.s.mutate('rename',dict(sid=self.sid,batch_id=self.bid,revision=0,tracking_no='x'))
        other=self.s.new('第二批次')['session']['id']
        with self.assertRaises(ValueError):self.s.mutate('delete_batch',dict(sid=other,batch_id=self.bid,revision=0))
        for n in [float('nan'),float('inf'),-1]:
            with self.assertRaises(ValueError):self.add('A',n)
        self.assertEqual(self.s.state(self.sid)['total'],1)
    def test_ambiguous_scan_no_silent_choice(self):
        self.s.lookup=lambda *a,**k:[dict(sku_id='A',name='A'),dict(sku_id='B',name='B')]
        self.batch('T');s=self.change('scan',code='BAR',qty=1);self.assertTrue(s['selection_required']);self.assertEqual(self.s.state(self.sid)['total'],0)
        s=self.change('scan',code='BAR',sku_id='B',qty=2);self.assertEqual(s['rows'][0]['sku_id'],'B')
    def test_csv_formula_safe_and_no_target_before_po(self):
        self.batch('=1+1');self.add('A',1);v=self.s.export(self.sid).decode('utf-8-sig');self.assertIn("'=1+1",v);self.assertNotIn('应收',v)
    def test_same_po_other_batch_cannot_duplicate_draft(self):
        self.receive();self.match();self.create()
        self.sid=self.s.new('second',owner='OWNER',auth='WAREHOUSE')['session']['id'];self.receive();self.match()
        with self.assertRaisesRegex(InboundError,'另一个'):self.create()
        self.assertEqual(self.j.creates,1)
    def test_backup_includes_independent_database(self):
        from backup import BackupManager
        import threading
        self.receive()
        m=BackupManager(self.tmp.name,threading.RLock()).create()
        self.assertEqual([r['name'] for r in m['files']],['unplanned.db'])
        self.assertTrue(m['complete'])
    def test_restart_preserves_tracking_and_jobs(self):
        self.receive();self.match();self.create();path=os.path.join(self.tmp.name,'unplanned.db');self.s.store.db.close()
        self.s=UnplannedService(path,self.j,lambda no:None,lambda *a,**k:[])
        self.assertEqual(self.s.state(self.sid)['total'],20);self.assertTrue(self.s.state(self.sid)['locked'])

if __name__=='__main__': unittest.main(verbosity=2)
