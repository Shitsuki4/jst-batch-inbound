import copy,json,tempfile,unittest
from pathlib import Path
from joint_receipt import JointService
from inbound_test import FakeJst
from inbound import InboundError
from jst import CallbackRejected

class JointFake(FakeJst):
    def __init__(self):
        super().__init__();self.pos={str(i):[dict(sku_id='A',name='商品A',qty=a,in_qty=0,poi_id='A'+str(i)),dict(sku_id='B',name='商品B',qty=b,in_qty=0,poi_id='B'+str(i))] for i,a,b in [(1,6,0),(2,4,5),(3,2,3)]}
    def fetch_detail(self,po_id,owner,auth):return {'datas':copy.deepcopy(self.pos[str(po_id)])}
    def callback(self,url,method,args,**kw):
        if method=='CreateFromPo': self.detail_rows=self.pos[str(args[0])]
        return super().callback(url,method,args,**kw)

class JointTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.j=JointFake();self.s=JointService(Path(self.tmp.name)/'joint.db',self.j,self.find,lambda *a,**k:[])
        self.sid=self.s.new(dict(purchase_nos=['1','2','3'],mother='123',confirmed=True))['session']['id'];self.bid=0
    def find(self,no):return dict(po_id=no,owner_co_id='OWNER',wms_co_id='WAREHOUSE'),dict(owner='OWNER',auth='WAREHOUSE')
    def tearDown(self):self.s.store.db.close();self.tmp.cleanup()
    def change(self,a,**kw):return self.s.mutate(a,dict(sid=self.sid,batch_id=self.bid,revision=self.s.state(self.sid)['meta']['revision'],**kw))
    def batch(self,no):self.bid=self.change('tracking',tracking_no=no,confirmed=True)['current_batch']
    def add(self,sku,n):return self.change('manual',sku_id=sku,name='商品'+sku,sku_code='',spec='',qty=n)
    def receive(self):
        for no,a,b in [('123-1',4,2),('123-2',3,4),('123-3',3,2)]:self.batch(no);self.add('A',a);self.add('B',b)
    def allocations(self):
        report=self.s.state(self.sid);pids=[p['id'] for p in report['purchases']];out=[]
        for b in report['batches']:
            for l in b['lines']:out.append(dict(line_id=l['id'],purchase_id=pids[0] if l['sku_id']=='A' else pids[1],qty=l['available']))
        return out
    def plan(self):return self.change('plan',mode='purchase_only',allocations=self.allocations(),confirmed=True)
    def test_merged_summary_and_history(self):
        self.receive();r=self.s.state(self.sid);rows={r['sku_id']:r for r in r['rows']}
        self.assertEqual((rows['A']['plan'],rows['A']['recv'],rows['A']['delta']),(12,10,-2));self.assertEqual((rows['B']['plan'],rows['B']['recv'],rows['B']['kind']),(8,8,'good'))
        for q in ['1','123','123-2',r['session']['input_no']]:self.assertEqual(len(self.s.history(q)),1)
        self.assertEqual(r['totals'],dict(short=2,over=0,extra=0));self.assertEqual(r['group']['expected'],None)
    def test_initial_baseline_not_live_or_prior_inbound(self):
        self.j.pos['1'][0]['in_qty']=3
        r=self.s.new(dict(purchase_nos=['1'],mother='NEXT',confirmed=True));sid=r['session']['id'];self.assertEqual(r['rows'][0]['plan'],3)
        self.j.pos['1'][0]['qty']=100;r=self.s.refresh(sid);self.assertEqual(r['rows'][0]['plan'],3);self.assertTrue(r['purchases'][0]['changed'])
    def test_tracking_confirmation_dedup_and_cross_group(self):
        with self.assertRaises(ValueError):self.change('tracking',tracking_no='123-1')
        self.batch('123-1');old=self.bid;self.batch('123-1');self.assertEqual(old,self.bid)
        sid=self.s.new(dict(purchase_nos=['1'],mother='OTHER',confirmed=True))['session']['id']
        with self.assertRaises(ValueError):self.s.mutate('tracking',dict(sid=sid,revision=0,tracking_no='123-1',confirmed=True))
    def test_identity_and_owner_isolation(self):
        self.j.coid='OTHER'
        with self.assertRaises(ValueError):self.s.state(self.sid)
        self.assertEqual(self.s.history(),[])
        self.j.coid='WAREHOUSE';self.s.find_purchase=lambda no:(dict(po_id=no),dict(owner=no,auth='WAREHOUSE'))
        with self.assertRaises(ValueError):self.s.new(dict(purchase_nos=['1','2'],mother='BAD',confirmed=True))
    def test_unknown_not_zero_and_all_or_none_refresh(self):
        del self.j.pos['1'][0]['in_qty'];sid=self.s.new(dict(purchase_nos=['1'],mother='UNKNOWN',confirmed=True))['session']['id'];r=self.s.state(sid);self.assertIsNone(r['rows'][0]['plan']);self.assertEqual(r['rows'][0]['kind'],'unknown')
        self.j.pos['2']=[]
        before=self.s.state(self.sid)['purchases']
        with self.assertRaises(ValueError):self.s.refresh(self.sid)
        self.assertEqual(self.s.state(self.sid)['purchases'],before)
    def test_allocation_validation_and_locking(self):
        self.receive();a=self.allocations();a.append(a[0])
        with self.assertRaises(ValueError):self.change('plan',mode='purchase_only',allocations=a,confirmed=True)
        self.assertFalse(self.s.state(self.sid)['stages'])
        r=self.plan();self.assertEqual(len(r['stages']),2)
        with self.assertRaises(ValueError):self.plan()
        line=r['batches'][0]['lines'][0];self.bid=r['batches'][0]['id']
        with self.assertRaises(ValueError):self.change('delete_line',line_id=line['id'])
        with self.assertRaises(ValueError):self.change('edit',line_id=line['id'],sku_id='A',name='商品A',sku_code='',spec='',qty=0)
        # Additional real quantity can be recorded without reusing occupied quantities.
        self.add('A',1);self.assertEqual(self.s.state(self.sid)['batches'][0]['lines'][0]['available'],1)
        self.change('cancel',stage_sid=r['stages'][0]['sid']);self.assertEqual(self.s.state(self.sid)['batches'][0]['lines'][0]['reserved'],0)
    def test_integer_only_quantities_and_allocations(self):
        self.batch('123-1')
        for n in [1.5,'2.5',True,None,-1]:
            with self.assertRaises(ValueError,msg=repr(n)):self.add('A',n)
        self.add('A',4);self.add('B','2')
        line=self.s.state(self.sid)['batches'][0]['lines'][0]
        with self.assertRaises(ValueError):self.change('edit',line_id=line['id'],sku_id='A',name='商品A',sku_code='',spec='',qty=3.5)
        pid=self.s.state(self.sid)['purchases'][0]['id']
        for n in [1.5,'0.5',True,0,'',None,-2]:
            with self.assertRaises(ValueError,msg=repr(n)) as cm:self.change('plan',mode='purchase_only',allocations=[dict(line_id=line['id'],purchase_id=pid,qty=n)],confirmed=True)
            self.assertIn('整数',str(cm.exception))
        self.assertEqual(self.s.state(self.sid)['stages'],[])
        r=self.change('plan',mode='purchase_only',allocations=[dict(line_id=line['id'],purchase_id=pid,qty='3')],confirmed=True)
        self.assertEqual(r['stages'][0]['total'],3)
        # Reserved line: decimal edit rejected before the reservation comparison
        with self.assertRaises(ValueError):self.change('edit',line_id=line['id'],sku_id='A',name='商品A',sku_code='',spec='',qty='3.5')
    def test_legacy_decimal_rows_readable(self):
        self.batch('123-1');self.add('A',4)
        self.s.store.db.execute("UPDATE line SET qty=2.5 WHERE session_id=?",(self.sid,));self.s.store.db.commit()
        r=self.s.state(self.sid);self.assertEqual(r['total'],2.5);self.assertEqual(r['batches'][0]['lines'][0]['available'],2.5)
        self.assertTrue(self.s.export(self.sid));self.assertEqual(self.s.history()[0]['total'],2.5)
    def test_group_outside_two_modes_and_explicit_target(self):
        self.batch('X1');self.add('X',2);r=self.s.state(self.sid);self.assertEqual(next(r for r in r['rows'] if r['sku_id']=='X')['kind'],'extra')
        a=[dict(line_id=r['batches'][0]['lines'][0]['id'],purchase_id=r['purchases'][0]['id'],qty=2)]
        with self.assertRaises(ValueError):self.change('plan',allocations=a,mode='purchase_only',confirmed=True)
        r=self.change('plan',allocations=a,mode='all_received',confirmed=True);self.assertEqual(r['stages'][0]['total'],2)
    def test_partial_success_unknown_and_restart(self):
        self.receive();r=self.plan();one,two=r['stages'];p=self.s.inbound.preview(one['sid'],'3');j=self.s.inbound.create(one['sid'],p['fingerprint'],'3',mode=one['mode']);self.assertEqual(j['status'],'complete')
        self.s.inbound.create(one['sid'],p['fingerprint'],'3',mode=one['mode']);self.assertEqual(self.j.creates,1)
        with self.assertRaises(ValueError):self.change('cancel',stage_sid=one['sid'])
        self.j.lose_create=True;p=self.s.inbound.preview(two['sid'],'3')
        with self.assertRaises(InboundError):self.s.inbound.create(two['sid'],p['fingerprint'],'3',mode=two['mode'])
        self.assertEqual(self.s.store.inbound_jobs(two['sid'])[0]['status'],'unknown')
        with self.assertRaises(ValueError):self.change('cancel',stage_sid=two['sid'])
        before=self.s.reserved(self.sid);path=self.s.store.path if hasattr(self.s.store,'path') else Path(self.tmp.name)/'joint.db';self.s.store.db.close();self.s=JointService(path,self.j,self.find,lambda *a,**k:[]);self.assertEqual(self.s.reserved(self.sid),before)
        self.assertEqual(self.s.state(self.sid)['stages'][0]['jobs'][0]['io_id'],j['io_id'])
        for _,method,_,_ in self.j.calls:self.assertNotIn(method,('ConfirmBatch','UpdatePrice','UpdateCost'))
    def test_unknown_resume_checks_account_and_reuses_original(self):
        self.receive();st=self.plan()['stages'][0]
        self.j.lose_create=True;p=self.s.inbound.preview(st['sid'],'3')
        with self.assertRaises(InboundError):self.s.inbound.create(st['sid'],p['fingerprint'],'3',mode=st['mode'])
        job=self.s.store.inbound_jobs(st['sid'])[0];io=str(8000+self.j.creates);old=self.j.uid
        self.j.uid='DIFFERENT'
        with self.assertRaises(ValueError):self.s.inbound.resume(job['id'],io)
        self.j.uid=old;self.j.lose_create=False
        resumed=self.s.inbound.resume(job['id'],io)
        self.assertEqual(resumed['status'],'complete');self.assertEqual(resumed['io_id'],io);self.assertEqual(self.j.creates,1)
        self.assertEqual(self.s.inbound.resume(job['id'])['io_id'],io)

    def test_changed_purchase_invalidates_preview(self):
        self.receive();r=self.plan();st=r['stages'][0];self.j.pos[st['po_id']][0]['qty']=17
        with self.assertRaises(ValueError):self.s.inbound.preview(st['sid'],'3')
        self.assertEqual(self.j.creates,0)
    def test_no_receipt_clone_export_and_duplicate_purchase(self):
        self.receive();self.plan();self.assertEqual(self.s.state(self.sid)['total'],18)
        self.assertIn('123-2',self.s.export(self.sid).decode('utf-8-sig'))
    def test_export_has_tracking_scan_time(self):
        import csv,io,re
        self.receive();self.plan()
        b=self.s.state(self.sid)['batches'][0]
        self.s.store.db.execute('UPDATE batch SET first_scan_at=? WHERE id=?',('2026-10-17 20:46:53',b['id']));self.s.store.db.commit()
        rows=list(csv.reader(io.StringIO(self.s.export(self.sid).decode('utf-8-sig'))))
        i=rows.index(['物流单','扫描登记时间','编码','实收','已占用','未分配'])
        mine=[r for r in rows[i+1:] if r and r[0]==b['tracking_no']]
        self.assertTrue(mine);self.assertTrue(all(r[1]=='2026/10/17 20:46:53' for r in mine))
        self.s.store.db.execute("UPDATE batch SET first_scan_at='' WHERE id=?",(b['id'],));self.s.store.db.commit()
        rows=list(csv.reader(io.StringIO(self.s.export(self.sid).decode('utf-8-sig'))))
        mine=[r for r in rows[i+1:] if r and r[0]==b['tracking_no']]
        self.assertTrue(all(re.fullmatch(r'\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}',r[1]) or r[1]=='' for r in mine))
        self.assertEqual(len(self.s.new(dict(purchase_nos=['1','1'],mother='DEDUP',confirmed=True))['purchases']),1)

if __name__=='__main__':unittest.main()
