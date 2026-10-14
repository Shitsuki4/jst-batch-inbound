# -*- coding: utf-8 -*-
"""盘点离线回归，绝不访问聚水潭。"""
import copy
import json
import os
import tempfile
import unittest
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch
from inventory import InventoryAPI, InventoryService, InventoryError, number
from inbound import InboundService, InboundError
from inbound_test import FakeJst as InboundFake
from jst import CallbackRejected, JstClient
from store import Store


class StockFake(InboundFake):
    uid, coid = '9001', '1500'
    uname, coname = '离线测试', '模拟仓库公司'

    def __init__(self):
        super().__init__()
        self.stock = [self.make_row('11','A',10,'BIN-1'), self.make_row('12','A',7,'BIN-2'), self.make_row('13','B',5,'BIN-3')]
        self.writes = []
        self.decimal = False
        self.fail = ''
        self.reject = False
        self.wrong_scope = False
        self.bad_pagination = False
        self.pagesize = 50
        self.catalog_calls = 0

    @staticmethod
    def make_row(rid, sku, qty, bin):
        return {'pack_item_id':rid,'sku_id':sku,'sku_code':'69001' if sku=='A' else '69002',
                'name':'测试商品 '+sku,'properties_value':'规格1','qty':qty,'bin':bin,'pack_id':None,
                'sys_pack_id':'100'+rid,'pack_type':'仓位','wh_id':'主仓','whId':1,'owner_co_id':1200,'co_id':1500,'freeze_qty':0}

    def partners(self): return [{'id':'1200','name':'模拟货主'},{'id':'2147483647','name':'所有商家'}]
    def wms_list(self): return [{'id':'1500','name':'模拟仓库公司'},{'id':'2147483647','name':'所有仓库'}]

    def _request(self, url, **kw):
        if '/wms/Pack/' not in url: return super()._request(url, **kw)
        return '''<input id="wh_1" value="1"><label for="pack_type_wh_1" class="txt">主仓</label>
                  <label for="pack_type_wh_3" class="txt">进货仓</label><label for="pack_type_wh_4" class="txt">次品仓</label>
                  <script>var _IsQtyUseDecimal = %s;</script>''' % str(self.decimal).lower()

    def callback(self, url, method, args, **kw):
        if '/wms/Pack/' not in url: return super().callback(url, method, args, **kw)
        q = parse_qs(urlparse(url).query)
        assert q['owner_co_id']==['1200'] and q['authorize_co_id']==['1500']
        assert kw['fields']['owner_co_id']=='1200' and kw['fields']['authorize_co_id']=='1500'
        self.calls.append((url,method,args,kw))
        if method == 'LoadDataToJSON':
            assert 'call_control' not in kw
            filters=json.loads(args[1]); key=filters[0]['k']; v=filters[0]['v']; wh=filters[1]['v']
            assert filters[1]['k']=='[p].wh_id'
            field={'[pit].sku_id':'sku_id','sku_code':'sku_code'}[key]
            assert filters[0]['c']==('@=' if field=='sku_id' else '=')
            rows=[copy.deepcopy(r) for r in self.stock if str(r[field])==v and str(r['whId'])==wh]
            if self.wrong_scope and rows: rows[0]['owner_co_id']=999
            page=int(args[0]); a=(page-1)*self.pagesize
            return {'datas':rows[a:a+self.pagesize], 'dp':{} if self.bad_pagination else {'IsLast':a+self.pagesize>=len(rows)}}
        assert method=='ModifyStockByPackItems' and kw['call_control']=='{page}'
        # Match the official JSON.stringify(arg) wire contract; arrays are rejected by ERP.
        assert len(args)==1 and isinstance(args[0],str), 'ERP requires Args[0] as JSON string'
        parsed=json.loads(args[0]); assert isinstance(parsed,list) and len(parsed)==1
        self.writes.append(copy.deepcopy(args))
        if self.reject: raise CallbackRejected('模拟权限拒绝')
        if self.fail=='before': raise TimeoutError('模拟发送失败，结果未知')
        arg=parsed[0]
        row=next(r for r in self.stock if str(r['pack_item_id'])==arg['PackItemId'])
        if self.fail!='ignore': row['qty']=arg['RQty']
        if self.fail=='after': raise TimeoutError('模拟写成功响应丢失')
        return True


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='jst-inventory-')
        self.path=os.path.join(self.tmp.name,'jst.db');self.st=Store(self.path)
        self.client=StockFake();self.svc=InventoryService(self.st,self.client)
    def tearDown(self): self.st.db.close();self.tmp.cleanup()
    def payload(self, **kw):
        b={'owner':'1200','auth':'1500','wh_id':'1','code':'A','code_type':'sku',
           'pack_item_id':'11','expected_qty':'10','mode':'delta','value':'1'}
        b.update(kw);return b
    def preview(self, **kw): return self.svc.preview(self.payload(**kw))
    def test_parse_config(self):
        c=self.svc.config('1200','1500');self.assertEqual(c['warehouses'][2],{'id':'4','name':'次品仓'});self.assertFalse(c['decimal'])
    def test_exact_rows_no_product_api(self):
        rows=self.svc.lookup('1200','1500','1','A');self.assertEqual(len(rows),2)
        self.assertEqual([r['qty'] for r in rows],['10','7']);self.assertNotIn('__KeyData',rows[0]);self.assertEqual(self.client.catalog_calls,0)
    def test_barcode_rows_are_explicit_not_merged(self):
        self.client.stock[2]['sku_code']='69001'
        r=self.svc.lookup('1200','1500','1','69001','barcode');self.assertEqual(len(r),3)
        p=self.preview(code='69001',code_type='barcode',pack_item_id='13',expected_qty='5')
        j=self.svc.submit(p['token']);self.assertEqual(j['status'],'verified')
        self.assertEqual([r['qty'] for r in self.client.stock],[10,7,6])
    def test_positive_one_only_selected_bin(self):
        p=self.preview();j=self.svc.submit(p['token']);self.assertEqual(j['status'],'verified')
        self.assertEqual([r['qty'] for r in self.client.stock],[11,7,5]);self.assertEqual(json.loads(self.client.writes[0][0]),[{'PackItemId':'11','RQty':11}])
    def test_negative_one_and_absolute_zero(self):
        j=self.svc.submit(self.preview(value='-1')['token']);self.assertEqual(j['snapshot']['target_qty'],'9')
        j=self.svc.submit(self.preview(mode='set',value='0',expected_qty='9')['token']);self.assertEqual(self.client.stock[0]['qty'],0)
    def test_absolute_not_plus_and_negative_baseline(self):
        self.client.stock[0]['qty']=-2
        j=self.svc.submit(self.preview(mode='set',value='20',expected_qty='-2')['token']);self.assertEqual(j['snapshot']['target_qty'],'20')
    def test_invalid_quantities(self):
        for v in ('NaN','Infinity','-Infinity','1e2','1.00001','',None,True,'1,000'):
            with self.subTest(v=v), self.assertRaises(InventoryError):self.preview(value=v)
        for v in ('-11','2147483648'):
            with self.assertRaises(InventoryError):self.preview(value=v)
        with self.assertRaises(InventoryError):self.preview(value='0.5')
        self.assertEqual(self.client.writes,[])
    def test_decimal_precision(self):
        self.client.decimal=True
        p=self.preview(value='0.0001');j=self.svc.submit(p['token']);self.assertEqual(j['snapshot']['target_qty'],'10.0001');self.assertEqual(j['status'],'verified')
    def test_unchanged_not_written(self):
        with self.assertRaisesRegex(InventoryError,'未变化'):self.preview(mode='set',value='10')
        with self.assertRaisesRegex(InventoryError,'未变化'):self.preview(value='0')
    def test_all_owner_auth_and_no_login(self):
        for k,v in (('owner','2147483647'),('auth','2147483647'),('owner','0'),('auth',''),('owner','bad')):
            with self.assertRaises(InventoryError):self.preview(**{k:v})
        self.client.uid=''
        with self.assertRaises(InventoryError):self.preview()
        self.assertEqual(self.client.writes,[])
    def test_wrong_scope_fails_closed(self):
        self.client.wrong_scope=True
        with self.assertRaisesRegex(InventoryError,'上下文'):self.preview()
    def test_no_ambiguous_wildcard_or_missing_id(self):
        for code in ('A,B','A*','',None):
            with self.assertRaises(InventoryError):self.preview(code=code)
        with self.assertRaises(InventoryError):self.preview(pack_item_id='')
        with self.assertRaises(InventoryError):self.preview(wh_id='8')
    def test_restricted_bins(self):
        for b in ('盘亏位','补货暂存位','bin.1.add'):
            self.client.stock[0]['bin']=b
            with self.assertRaises(InventoryError):self.preview()
    def test_pages_and_invalid_pagination(self):
        self.client.pagesize=1
        self.assertEqual(len(self.svc.lookup('1200','1500','1','A')),2)
        self.client.bad_pagination=True
        with self.assertRaises(InventoryError):self.preview()
    def test_stale_before_preview(self):
        self.client.stock[0]['qty']=13
        with self.assertRaisesRegex(InventoryError,'变化'):self.preview()
        self.assertEqual(self.client.writes,[])
    def test_stale_before_submit(self):
        p=self.preview();self.client.stock[0]['qty']=13
        with self.assertRaisesRegex(InventoryError,'变化'):self.svc.submit(p['token'])
        self.assertEqual(self.client.writes,[]);self.assertEqual(self.svc.get(p['token'])['status'],'stale')
    def test_expired_preview(self):
        p=self.preview()
        with patch('inventory.time.time',return_value=p['created']+121):
            with self.assertRaises(InventoryError):self.svc.submit(p['token'])
        self.assertEqual(self.client.writes,[])
    def test_duplicate_confirm_and_restart(self):
        p=self.preview();self.svc.submit(p['token']);self.st.db.close();self.st=Store(self.path);self.svc=InventoryService(self.st,self.client)
        self.assertEqual(self.svc.submit(p['token'])['status'],'verified');self.assertEqual(len(self.client.writes),1)
    def test_unknown_after_write_no_replay_then_verify(self):
        p=self.preview();self.client.fail='after';self.assertEqual(self.svc.submit(p['token'])['status'],'unknown')
        self.assertEqual(self.svc.submit(p['token'])['status'],'unknown')
        with self.assertRaisesRegex(InventoryError,'待核实'):self.preview(expected_qty='11')
        self.assertEqual(self.svc.verify(p['token'])['status'],'verified');self.assertEqual(len(self.client.writes),1)
    def test_unknown_before_write_stays_locked_after_restart(self):
        p=self.preview();self.client.fail='before';self.svc.submit(p['token']);self.st.db.close();self.st=Store(self.path);self.svc=InventoryService(self.st,self.client)
        self.assertEqual(self.svc.verify(p['token'])['status'],'unknown')
        with self.assertRaisesRegex(InventoryError,'待核实'):self.preview()
        self.assertEqual(len(self.client.writes),1)
    def test_two_previews_cannot_bypass_unknown_lock(self):
        a=self.preview();b=self.preview();self.client.fail='before';self.svc.submit(a['token'])
        with self.assertRaisesRegex(InventoryError,'未决'):self.svc.submit(b['token'])
        self.assertEqual(len(self.client.writes),1)
    def test_crash_sending_never_replayed(self):
        p=self.preview();self.svc.update(p['token'],'sending')
        self.assertEqual(self.svc.submit(p['token'])['status'],'sending');self.assertEqual(self.client.writes,[])
        self.assertEqual(self.svc.verify(p['token'])['status'],'unknown')
    def test_rejected_can_new_preview_not_same_token(self):
        p=self.preview();self.client.reject=True;self.assertEqual(self.svc.submit(p['token'])['status'],'rejected')
        self.svc.submit(p['token']);self.assertEqual(len(self.client.writes),1)
        self.assertEqual(self.preview()['status'],'preview')
    def test_verify_mismatch_not_false_success(self):
        p=self.preview();self.client.fail='ignore';self.assertEqual(self.svc.submit(p['token'])['status'],'unknown')
    def test_row_moved_between_preview_and_submit(self):
        p=self.preview();self.client.stock[0]['bin']='new-bin'
        with self.assertRaises(InventoryError):self.svc.submit(p['token'])
        self.assertEqual(self.client.writes,[])
    def test_different_account_cannot_use_token_or_history(self):
        p=self.preview();self.client.uid='9002'
        with self.assertRaises(InventoryError):self.svc.submit(p['token'])
        self.assertEqual(self.svc.history(),[])


class WarehouseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.st=Store(os.path.join(self.tmp.name,'jst.db'))
        self.c=StockFake();self.svc=InboundService(self.st,self.c)
        self.sid=self.st.open_session('123','123','po_id',{'owner_co_id':'1200','auth_co_id':'1500'},[{'sku_id':'A','qty':100}])
        batch,_=self.st.ensure_batch(self.sid,'T1');self.st.add_qty(batch['id'],self.sid,{'sku_id':'A','name':'A','matched':True},2)
    def tearDown(self):self.st.db.close();self.tmp.cleanup()
    def create(self, wid='4'):
        p=self.svc.preview(self.sid,wid);return self.svc.create(self.sid,p['fingerprint'],wid)
    def test_target_warehouse_keeps_all_context_and_fields(self):
        j=self.create();h=self.c.docs[j['io_id']]['head']
        self.assertEqual(h['wh_id'],'4');self.assertEqual(h['owner_co_id'],'1200');self.assertEqual(h['wms_co_id'],'1500')
        self.assertEqual(h['remark'],'保留原备注');self.assertEqual(j['status'],'complete')
        self.assertNotIn('ConfirmBatch',[c[1] for c in self.c.calls])
        self.assertTrue(any(c[1]=='Save' and 'call_control' not in c[3] for c in self.c.calls))
        added=next(c for c in self.c.calls if c[1]=='AddItemsUnify');self.assertTrue(added[2][0].endswith('-new'))
    def test_main_warehouse_needs_no_save(self):
        self.create('1');self.assertNotIn('Save',[c[1] for c in self.c.calls])
    def test_warehouse_in_fingerprint(self):
        a=self.svc.preview(self.sid,'1');b=self.svc.preview(self.sid,'4');self.assertNotEqual(a['fingerprint'],b['fingerprint'])
        with self.assertRaises(InboundError):self.svc.create(self.sid,a['fingerprint'],'4')
        self.assertEqual(self.c.creates,0)
    def test_invalid_warehouse(self):
        for v in ('', '100'):
            with self.assertRaises(InboundError):self.svc.preview(self.sid,v)
    def test_all_owner_and_authorize_are_rejected(self):
        for field in ('owner_co_id','auth_co_id'):
            snap={'owner_co_id':'1200','auth_co_id':'1500'};snap[field]='2147483647'
            with self.assertRaisesRegex(InboundError,'所有'):self.svc.context(snap)
    def test_wrong_owner_or_company_on_bind(self):
        self.c.lose_create=True;p=self.svc.preview(self.sid,'4')
        with self.assertRaises(InboundError):self.svc.create(self.sid,p['fingerprint'],'4')
        job=self.st.inbound_jobs(self.sid)[0];self.c.docs['8001']['head']['owner_co_id']='7777'
        with self.assertRaisesRegex(InboundError,'货主'):self.svc.resume(job['id'],'8001')
    def test_saved_warehouse_mismatch_does_not_complete(self):
        orig=self.c.callback
        def call(url,method,args,**kw):
            result=orig(url,method,args,**kw)
            if method=='Save':result['data']['wh_id']='3'
            return result
        self.c.callback=call
        with self.assertRaises(InboundError):self.create()
        self.assertEqual(self.st.inbound_jobs(self.sid)[0]['status'],'pending')
        self.assertEqual(self.c.creates,1)
    def test_save_response_lost_resume_same_doc(self):
        orig=self.c.callback;once=[True]
        def call(url,method,args,**kw):
            result=orig(url,method,args,**kw)
            if method=='Save' and once[0]:once[0]=False;raise TimeoutError('换仓成功响应丢失')
            return result
        self.c.callback=call
        with self.assertRaises(InboundError):self.create()
        job=self.st.inbound_jobs(self.sid)[0];self.assertEqual(self.svc.resume(job['id'])['status'],'complete');self.assertEqual(self.c.creates,1)




class InventoryWireTests(unittest.TestCase):
    def test_official_callback_wire_uses_json_string(self):
        # Exercise real JstClient.callback serialization, not just a mirrored business fake.
        client=JstClient();client.uid='9001';client.coid='1500'
        client._viewstate=lambda url:('TEST_VIEWSTATE','TEST_GENERATOR')
        sent=[]
        def request(url,body=None,**kw):
            sent.append((url,parse_qs(body)))
            return json.dumps({'IsSuccess':True,'ReturnValue':True})
        client._request=request
        api=InventoryAPI(client,'1200','1500')
        for value in ('11','9','25','10.1250'):
            api.write('11',value)
            url,form=sent[-1]
            self.assertIn('am___=ModifyStockByPackItems',url)
            self.assertEqual(form['owner_co_id'],['1200']);self.assertEqual(form['authorize_co_id'],['1500'])
            param=json.loads(form['__CALLBACKPARAM'][0])
            self.assertEqual(param['Method'],'ModifyStockByPackItems')
            self.assertEqual(param['CallControl'],'{page}')
            self.assertEqual(len(param['Args']),1)
            self.assertIsInstance(param['Args'][0],str)
            data=json.loads(param['Args'][0])
            self.assertEqual(data,[{'PackItemId':'11','RQty':float(value)}])
            self.assertIsInstance(data[0]['PackItemId'],str)
    def test_erp_type_rejection_is_not_retried(self):
        client=StockFake()
        with self.assertRaisesRegex(AssertionError,'JSON string'):
            client.callback('https://test/wms/Pack/?owner_co_id=1200&authorize_co_id=1500','ModifyStockByPackItems',[[{'PackItemId':'11','RQty':11}]],fields={'owner_co_id':'1200','authorize_co_id':'1500'},call_control='{page}')
        self.assertEqual(client.writes,[])
        self.assertEqual(client.stock[0]['qty'],10)

if __name__=='__main__':unittest.main(verbosity=2)
