# -*- coding: utf-8 -*-
"""2.0.2 offline integration. Never reads production data or writes ERP."""
import base64, copy, json, re, unittest, uuid
from catalog import scope_key, aliases
from codes import auxiliary_codes
from inventory import InventoryAPI, InventoryService, InventoryError
from jst import JstClient, ItemAmbiguousError
from unittest.mock import Mock
from urllib.parse import parse_qs
from inventory_test import StockFake
from pda_test import Fixture

class LocationFake(StockFake):
    DEFAULT='915195086000001001'  # deliberately not JS-safe
    def __init__(self):
        super().__init__()
        row=self.make_row('21','A',4,None)
        row.update(pack_id='通用暂存位',sys_pack_id=self.DEFAULT,pack_type='暂存位')
        self.stock.append(row)
        self.ignore_position=False
    def _request(self,url,**kw):
        return super()._request(url,**kw)+'<input id="pack_type_genaral_default_ack">'
    def callback(self,url,method,args,**kw):
        if method=='GetDefaultPackId':
            assert kw['call_control']=='{page}'
            return self.DEFAULT
        if method=='LoadDataToJSON':
            r=super().callback(url,method,args,**kw)
            if not self.ignore_position:
                for f in json.loads(args[1]):
                    if f['k']=='[p].pack_id':
                        assert f['v']==self.DEFAULT
                        r['datas']=[x for x in r['datas'] if x['sys_pack_id']==f['v']]
                    if f['k']=='pack_type': r['datas']=[x for x in r['datas'] if x['bin']]
            return r
        return super().callback(url,method,args,**kw)

class ScanUXTests(unittest.TestCase):
    def setUp(self):
        self.f=Fixture();self.s=self.f.server;self.f.pair()
        self.s.JST=LocationFake();self.s.INVENTORY=InventoryService(self.s.STORE,self.s.JST)
        self.key=scope_key('1500','1200','1500')
        self.s.CATALOG.replace(self.key,'模拟货主',[
            {'sku_id':'A','sku_code':'690000000001','name':'A'},
            {'sku_id':'X','sku_code':'690000000001','aux_codes':'ALT-X,SHARED','name':'外部X'},
            {'sku_id':'Y','sku_code':'690003','aux_codes':'SHARED,ALT-Y','name':'外部Y'}])
    def tearDown(self): self.f.close()
    def body(self,code,**kw):
        return dict(sid=self.f.sid,batch_id=self.f.batch['id'],code=code,qty=0,**kw)
    def scan(self,code): return self.f.call('/api/item/scan',self.body(code))
    def choose(self,code,it,pda=False):
        b=self.body(code);b.update(item=it,qty=2)
        if pda: return self.f.http_call('/api/item/choose',b,{'X-Request-ID':uuid.uuid4().hex})[1]
        return self.f.call('/api/item/choose',b)
    def test_real_aux_format_and_defensive_normalization(self):
        self.assertEqual(auxiliary_codes('6974445741559,6934713004441,6934713005516'),['6974445741559','6934713004441','6934713005516'])
        self.assertIn('alt-y',aliases({'aux_codes':['ALT-X',{'code':'ALT-Y'}]}))
        self.assertEqual(auxiliary_codes(' A，B;C；D\nE '),list('ABCDE'))
    def test_purchase_does_not_hide_outside_collision(self):
        r=self.scan('690000000001');self.assertTrue(r['multi'],r)
        self.assertEqual({i['sku_id'] for i in r['candidates']},{'A','X'})
        outside=next(i for i in r['candidates'] if i['sku_id']=='X')
        self.assertTrue(outside['outside_purchase'])
        r=self.choose('690000000001',outside);self.assertTrue(r['ok'],r);self.assertTrue(r['unmatched'])
        line=next(x for x in r['lines'] if x['sku_id']=='X');self.assertFalse(line['matched'])
        self.assertEqual(line['qty'],2)
    def test_outside_only_aux_candidates_pda_and_dedup(self):
        r=self.scan('SHARED');self.assertTrue(r['multi']);self.assertEqual(len(r['candidates']),2)
        chosen=next(x for x in r['candidates'] if x['sku_id']=='Y')
        r=self.choose('SHARED',chosen,pda=True);self.assertTrue(r['ok'],r);self.assertTrue(r['unmatched'])
        self.assertEqual(self.scan('ALT-X')['item']['sku_id'],'X')
        self.s.CATALOG.replace(self.key,'test',[{'sku_id':'A','sku_code':'690000000001','aux_codes':'690000000001'}])
        self.assertFalse(self.scan('690000000001').get('multi'))
    def test_choose_untrusted_metadata_and_wrong_batch(self):
        it=next(x for x in self.scan('SHARED')['candidates'] if x['sku_id']=='X')
        it.update(name='伪造',matched=True,outside_purchase=False)
        r=self.choose('SHARED',it);self.assertEqual(r['item']['name'],'外部X');self.assertTrue(r['unmatched'])
        b=self.body('SHARED');b.update(item=it,qty=1,batch_id=9999)
        self.assertFalse(self.f.call('/api/item/choose',b)['ok'])
    def test_stale_candidate_rejected_without_increment(self):
        it=self.scan('SHARED')['candidates'][0]
        old=self.s.STORE.progress(self.f.sid)['recv']
        self.s.CATALOG.replace(self.key,'test',[{'sku_id':'Z','aux_codes':'SHARED'}])
        self.assertFalse(self.choose('SHARED',it)['ok'])
        self.assertEqual(old,self.s.STORE.progress(self.f.sid)['recv'])
    def test_summary_distinct_sku_and_barcode(self):
        row=self.s.STORE.po_received_summary(self.f.sid)[0]
        self.assertEqual(row['sku_id'],'A');self.assertEqual(row['sku_code'],'690000000001')
    def unknown_line(self, code="NO-CATALOG"):
        b=self.body(code);b['qty']=2
        r=self.f.call('/api/item/scan',b);self.assertTrue(r['ok'],r)
        return next(l for l in r['lines'] if l['sku_id']==code)
    def edit_body(self,line,**changes):
        values={k:line[k] or '' for k in ('sku_id','name','sku_code','spec')};values.update(changes)
        return dict(line_id=line['id'],sid=line['session_id'],batch_id=line['batch_id'],expected=line,values=values)
    def test_unknown_can_receive_edit_and_rescan_without_catalog(self):
        line=self.unknown_line()
        r=self.f.call('/api/line/edit',self.edit_body(line,name='手工商品',spec='大号',sku_code='690999'))
        self.assertTrue(r['ok'],r);self.assertEqual(r['line']['qty'],2);self.assertFalse(r['line']['matched'])
        b=self.body('NO-CATALOG');b['qty']=1
        r=self.f.call('/api/item/scan',b);line=next(l for l in r['lines'] if l['sku_id']=='NO-CATALOG')
        self.assertEqual((line['qty'],line['name'],line['spec']),(3,'手工商品','大号'))
    def test_edit_encoding_rekeys_and_preserves_quantity(self):
        line=self.unknown_line()
        r=self.f.call('/api/line/edit',self.edit_body(line,sku_id='X',name='手改X'))
        self.assertTrue(r['ok'],r);self.assertEqual(r['line']['key'],'sku:RAW:X');self.assertEqual(r['line']['qty'],2)
        self.assertNotIn('NO-CATALOG',[l['sku_id'] for l in r['lines']])
        b=self.body('X');b['qty']=1
        r=self.f.call('/api/item/scan',b);self.assertEqual(next(l for l in r['lines'] if l['sku_id']=='X')['qty'],3)
    def test_duplicate_and_stale_edits_do_not_lose_receipts(self):
        line=self.unknown_line()
        r=self.f.call('/api/line/edit',self.edit_body(line,sku_id='A'));self.assertFalse(r['ok']);self.assertIn('已有该编码',r['msg'])
        self.s.STORE.set_qty(line['id'],5)
        r=self.f.call('/api/line/edit',self.edit_body(line,name='旧窗口'));self.assertFalse(r['ok']);self.assertIn('变化',r['msg'])
        now=next(l for l in self.s.STORE.batch_lines(line['batch_id']) if l['id']==line['id'])
        self.assertEqual(now['qty'],5);self.assertEqual(now['name'],line['name'])
    def test_edit_correct_purchase_sku_reclassifies_without_changing_baseline(self):
        before=self.s.STORE.get_session(self.f.sid)['items_json']
        batch,_=self.s.STORE.ensure_batch(self.f.sid,'NEW-MANUAL')
        lid,_=self.s.STORE.add_qty(batch['id'],self.f.sid,dict(key='RAW:TYPO',sku_id='TYPO',name='手工',matched=False),2)
        line=self.s.STORE.batch_lines(batch['id'])[0]
        r=self.f.call('/api/line/edit',self.edit_body(line,sku_id='A'));self.assertTrue(r['ok'],r)
        self.assertTrue(r['line']['matched']);self.assertEqual(r['line']['key'],'sku:A');self.assertEqual(r['line']['poi_id'],'1')
        self.assertEqual(self.s.STORE.get_session(self.f.sid)['items_json'],before)
        self.assertEqual(self.s.STORE.po_received_summary(self.f.sid)[0]['recv'],9)
    def test_edit_rejects_wrong_scope_invalid_fields_and_matched_lines(self):
        line=self.unknown_line();b=self.edit_body(line,name='safe')
        for delta in ({'sid':9999},{'batch_id':9999},{'line_id':9999},{'expected':None},{'values':[]},{'values':{'sku_id':''}}):
            self.assertFalse(self.f.call('/api/line/edit',dict(b,**delta))['ok'])
        for changes in ({'sku_id':''},{'name':''},{'name':'x'*301},{'sku_id':'a\nb'}):
            self.assertFalse(self.f.call('/api/line/edit',self.edit_body(line,**changes))['ok'])
        matched=next(l for l in self.s.STORE.batch_lines(line['batch_id']) if l['matched'])
        self.assertFalse(self.f.call('/api/line/edit',self.edit_body(matched,name='no'))['ok'])
    def test_pda_can_edit_unknown_but_cannot_edit_foreign_scope(self):
        line=self.unknown_line();b=self.edit_body(line,name='PDA手工')
        status,r,_=self.f.http_call('/api/line/edit',b);self.assertEqual(status,200);self.assertTrue(r['ok'],r)
        self.assertEqual(self.f.http_call('/receipt-editor.js')[0],200)
        self.s.STORE.db.execute("UPDATE session SET auth_co_id='999' WHERE id=?",(self.f.sid,));self.s.STORE.db.commit()
        self.assertFalse(self.f.http_call('/api/line/edit',b)[1]['ok'])
    def test_reserved_receipt_cannot_edit_metadata(self):
        line=self.unknown_line();b=self.edit_body(line,name='no')
        self.s.STORE.set_kv('inbound_validate_purchase','0')
        p=self.s.INBOUND.preview(self.f.sid,'1')
        self.s.STORE.reserve_inbound(p['snapshot'],p['fingerprint'],self.s.JST.coid)
        r=self.f.call('/api/line/edit',b);self.assertFalse(r['ok']);self.assertIn('入库',r['msg'])

    def lookup(self,location):
        return self.f.call('/api/inventory/lookup?owner=1200&auth=1500&wh_id=1&code=A&location='+location)
    def test_position_filters(self):
        self.assertEqual(len(self.lookup('all')['rows']),3)
        self.assertEqual(len(self.lookup('bin')['rows']),2)
        r=self.lookup('general');self.assertTrue(r['ok'],r)
        self.assertEqual([x['pack_item_id'] for x in r['rows']],['21'])
        self.assertFalse(self.lookup('guessed')['ok'])
    def test_general_preview_write_readback_and_moved_row_rejection(self):
        b=dict(owner='1200',auth='1500',wh_id='1',location='general',code='A',pack_item_id='21',expected_qty='4',mode='delta',value='-1')
        p=self.f.call('/api/inventory/preview',b);self.assertTrue(p['ok'],p)
        self.assertEqual(p['job']['snapshot']['position'],'通用暂存位')
        r=self.f.call('/api/inventory/submit',dict(token=p['job']['token'],confirmed=True))
        self.assertEqual(r['job']['status'],'verified');self.assertEqual([x['qty'] for x in self.s.JST.stock],[10,7,5,3])
        b['expected_qty']='3';p=self.f.call('/api/inventory/preview',b)
        self.s.JST.stock[-1]['sys_pack_id']='different'
        self.assertFalse(self.f.call('/api/inventory/submit',dict(token=p['job']['token'],confirmed=True))['ok'])
        self.assertEqual(len(self.s.JST.writes),1)
    def test_wrong_position_fail_closed(self):
        self.s.JST.ignore_position=True
        self.assertFalse(self.lookup('general')['ok'])
    def test_qr_payload_local_one_use(self):
        p=self.f.host.pairing()
        self.assertTrue(p['qr'].startswith('data:image/svg+xml;base64,'))
        svg=base64.b64decode(p['qr'].split(',')[1]).decode()
        self.assertIn('shape-rendering="crispEdges"',svg)
        self.assertNotIn('https://',svg)
        data=json.loads(base64.urlsafe_b64decode(p['text'][8:]));self.assertEqual(data['code'],p['code'])
        self.assertEqual(data['pin'],self.f.host.pin)
        self.f.host.pair(p['code'],'test')
        with self.assertRaises(ValueError):self.f.host.pair(p['code'],'again')

class ContextAndOnlineTests(unittest.TestCase):
    def test_delegated_warehouse_rows_keep_login_company(self):
        client=LocationFake()
        source=client._request('https://test/wms/Pack/')
        client._request=Mock(return_value=source+'<script>var _OwnerCoid = 1200; var ___RequestAuthorizeCoid = 1600;</script>')
        row=copy.deepcopy(client.stock[-1])
        def callback(url,method,args,**kw):
            self.assertEqual(parse_qs(url.split('?')[1]),{'owner_co_id':['1200'],'authorize_co_id':['1600']})
            self.assertEqual(kw['fields']['authorize_co_id'],'1600')
            if method=='GetDefaultPackId': return client.DEFAULT
            self.assertEqual(method,'LoadDataToJSON')
            return {'datas':[copy.deepcopy(row)],'dp':{'IsLast':True}}
        client.callback=Mock(side_effect=callback)
        api=InventoryAPI(client,'1200','1600')
        self.assertEqual(api.rows('A','1',location='general')[0]['co_id'],1500)
        for page in (source,source+'<script>var _OwnerCoid = 1300; var ___RequestAuthorizeCoid = 1600;</script>',source+'<script>var _OwnerCoid = 1200; var ___RequestAuthorizeCoid = 1700;</script>'):
            client._request.return_value=page
            before=client.callback.call_count
            with self.assertRaises(InventoryError):api.rows('A','1',location='general')
            self.assertEqual(client.callback.call_count,before)
        client._request.return_value=source+'<script>var _OwnerCoid = 1200; var ___RequestAuthorizeCoid = 1600;</script>'
        row['co_id']=1600
        with self.assertRaises(InventoryError):api.rows('A','1',location='general')

    def test_online_collisions_keep_auth_and_cache_scope(self):
        client=JstClient();client.coid='1500'
        def json_api(host,path,**kw):
            self.assertEqual(host,'apiweb')
            self.assertEqual(path,'ItemApi/ItemSku/GetPageListV2')
            self.assertEqual(parse_qs(kw['qs'])['owner_co_id'],['1200'])
            self.assertIn(parse_qs(kw['qs'])['authorize_co_id'][0],('1600','1700'))
            if 'sku_code' in kw['data']:
                return [{'sku_id':'A','sku_code':'69001','_sku_codes':'ALT-A,SHARED'},
                        {'sku_id':'X','sku_code':'69001','_sku_codes':'ALT-X,SHARED'},
                        {'sku_id':'NO','sku_code':'wrong'}]
            return []
        client.json_api=Mock(side_effect=json_api)
        client.item_detail=Mock(return_value=None)
        for auth in ('1600','1600','1700'):
            with self.assertRaises(ItemAmbiguousError) as cm:client.lookup_item('69001','1200',auth)
            self.assertEqual({x['sku_id'] for x in cm.exception.items},{'A','X'})
            self.assertIn('SHARED',cm.exception.items[0]['aux_codes'])
        self.assertEqual(client.json_api.call_count,4)
        client.item_detail.assert_any_call('69001','1200','1600')
        client.item_detail.assert_any_call('69001','1200','1700')

if __name__=='__main__':unittest.main(verbosity=2)
