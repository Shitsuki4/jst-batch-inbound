# -*- coding: utf-8 -*-
"""商品库离线回归：不调用真实 ERP，不使用真实数据。"""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs
from catalog import CatalogDB, CatalogSync, INTERVAL, RETRY, fetch_catalog, scope_key
from jst import JstClient


class FakeCatalog(JstClient):
    def __init__(self):
        super().__init__()
        self.uid, self.coid = 'U', 'C'
        self.pages = [[{'sku_id':'A', 'sku_code':'690001', '_sku_codes':'ALT1,ALT2', 'i_id':'STYLE', 'owner_co_id':'O'}], []]
        self.calls = []
        self.error = False
    def partners(self): return []
    def catalog_page(self, owner, auth, page, size=200):
        self.calls.append((owner,auth,page,size))
        if owner == 'C': return []
        if self.error: raise OSError('模拟断网')
        return self.pages[min(page-1,len(self.pages)-1)]


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = CatalogDB(Path(self.tmp.name)/'catalog.db')
        self.key = scope_key('C','O','W')
        self.fake = FakeCatalog()
    def tearDown(self): self.tmp.cleanup()
    def put(self):
        self.db.replace(self.key,'货主', [{'sku_id':'A','sku_code':'690001','aux_codes':'ALT1,ALT2','i_id':'STYLE','name':'商品A'}])
    def test_scope_isolation(self):
        self.put()
        for k in [scope_key('D','O','W'), scope_key('C','P','W'),scope_key('C','O','Z')]:
            self.assertEqual(self.db.lookup(k,'690001'), [])
        self.assertEqual(scope_key('C','0',''),scope_key('C','C','C'))
        self.assertFalse(scope_key('C','2147483647','W'))
    def test_aliases_and_persistence(self):
        self.put()
        reopened = CatalogDB(Path(self.tmp.name)/'catalog.db')
        for code in ['A','690001','alt1','ALT2','style']:
            self.assertEqual(reopened.lookup(self.key,code)[0]['sku_id'],'A')
    def test_duplicates_are_candidates(self):
        self.db.replace(self.key,'货主',[{'sku_id':'A','sku_code':'X'},{'sku_id':'B','sku_code':'X'}])
        self.assertEqual(len(self.db.lookup(self.key,'X')),2)
    def test_atomic_invalid_replacement(self):
        self.put()
        for rows in [[{'sku_id':'B'},{'sku_id':'b'}],[],[{}]]:
            with self.assertRaises(ValueError): self.db.replace(self.key,'货主',rows)
            self.assertEqual(self.db.lookup(self.key,'690001')[0]['sku_id'],'A')
    def test_success_replaces_deleted_aliases(self):
        self.put()
        self.db.point_put(self.key,'POINT',{'sku_id':'Q'})
        self.db.replace(self.key,'货主',[{'sku_id':'B'}])
        self.assertEqual(self.db.lookup(self.key,'690001'),[])
        self.assertFalse(self.db.point_get(self.key,'POINT')[0])
    def test_six_hours_and_failure_retry(self):
        self.put()
        ts=self.db.status('C')[0]['success']
        self.assertFalse(self.db.due(self.key,now=ts+INTERVAL-1))
        self.assertTrue(self.db.due(self.key,now=ts+INTERVAL))
        self.db.failed(self.key,'货主','断网')
        ts=self.db.status('C')[0]['attempt']
        self.assertFalse(self.db.due(self.key,now=ts+RETRY-1))
        self.assertTrue(self.db.due(self.key,now=ts+RETRY))
        self.assertTrue(self.db.lookup(self.key,'ALT1'))
    def test_point_ttl_and_isolation(self):
        with patch('catalog.time.time', return_value=1000): self.db.point_put(self.key,'X',None)
        with patch('catalog.time.time', return_value=1200): self.assertEqual(self.db.point_get(self.key,'x'),(True,None))
        with patch('catalog.time.time', return_value=1400): self.assertFalse(self.db.point_get(self.key,'x')[0])
        self.db.point_put(self.key,'Y',{'sku_id':'Z'})
        self.assertFalse(self.db.point_get(scope_key('D','O','W'),'Y')[0])
    def test_pagination_includes_auxiliary_and_short_pages(self):
        rows=fetch_catalog(self.fake,'O','W')
        self.assertEqual(rows[0]['aux_codes'],'ALT1,ALT2')
        self.assertEqual([c[2] for c in self.fake.calls],[1,2])
    def test_bad_pagination_rejected(self):
        for pages in [[{'oops':1}], [[{'sku_id':'A'}]], [[{'sku_id':''}]], [[{'sku_id':'A','owner_co_id':'OTHER'}]]]:
            self.fake.pages=pages
            with self.assertRaises(ValueError): fetch_catalog(self.fake,'O','W')
    def test_mid_download_cancelled(self):
        with self.assertRaises(RuntimeError): fetch_catalog(self.fake,'O','W',valid=lambda:False)
        self.assertFalse(self.fake.calls)
    def test_scheduler_initial_due_manual_and_failure(self):
        sync=CatalogSync(self.db,lambda:(self.fake,[('O','W','货主')],lambda:True))
        sync.run_once(); self.assertTrue(self.db.lookup(self.key,'ALT1'))
        n=len(self.fake.calls)
        sync.run_once(); self.assertEqual(len(self.fake.calls),n)
        self.fake.error=True
        sync.run_once(force=True)
        self.assertTrue(self.db.lookup(self.key,'ALT1'))
        self.assertIn('模拟断网', self.db.status('C')[1 if self.db.status('C')[0]['owner']=='C' else 0]['error'])
    def test_network_does_not_block_local_lookup(self):
        self.put(); entered=threading.Event(); release=threading.Event()
        original=self.fake.catalog_page
        def slow(*args): entered.set(); release.wait(5); return original(*args)
        self.fake.catalog_page=slow
        sync=CatalogSync(self.db,lambda:(self.fake,[('O','W','货主')],lambda:True))
        t=threading.Thread(target=lambda:sync.run_once(force=True)); t.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.db.lookup(self.key,'ALT1')[0]['sku_id'],'A')
        finally: release.set(); t.join(5)
        self.assertFalse(t.is_alive())
    def test_inbound_readback_searches_subwarehouses(self):
        from inbound import PurchaseInAPI
        calls=[]
        class Client:
            def callback(self,url,method,args,**kw):
                calls.append((method,args,kw))
                if method=='LoadWarehouse':
                    assert kw['call_control']=='{page}'
                    return {'1':'主仓','3':'进货仓','4':'次品仓'}
                filters=json.loads(args[1])
                wh=next((f['v'] for f in filters if f['k']=='wh_id'),'')
                return {'datas':[{'io_id':'8001','wh_id':'3','warehouse':'进货仓'}] if wh=='3' else []}
        row=PurchaseInAPI(Client(),'O','W').row('8001')
        self.assertEqual(row['wh_id'],'3')
        self.assertTrue(all(c[0] in ('LoadWarehouse','LoadDataToJSON') for c in calls))

    def test_wire_protocol(self):
        client=JstClient(); client.uid='U'; client.coid='C'; client.cookies={'token':'only-memory'}
        with patch.object(client,'json_api',return_value=[]) as call:
            client.catalog_page('O','W',3,200)
            args,kw=call.call_args
            self.assertEqual(args,('apiweb','ItemApi/ItemSku/GetPageListV2'))
            self.assertEqual(parse_qs(kw['qs']),{'owner_co_id':['O'],'authorize_co_id':['W']})
            self.assertEqual(kw['page']['currentPage'],3)
            self.assertIn('_sku_codes',kw['data']['queryFlds'])
            self.assertNotIn('enabled',kw['data'])
            self.assertNotIn('owner_co_id',kw['data'])
        clone=client.clone_for_catalog(); clone.cookies.clear()
        self.assertTrue(client.cookies)


class CatalogHTTPTests(unittest.TestCase):
    def setUp(self):
        from inbound_http_test import Fixture
        self.f=Fixture()
        self.s=self.f.server
        self.key=scope_key('WAREHOUSE','OWNER','WAREHOUSE')
        self.s.JST.lookup_item=lambda *a: (_ for _ in ()).throw(AssertionError('不应调用远端商品查询'))
        self.s.CATALOG.replace(self.key,'测试货主',[{'sku_id':'A','sku_code':'OTHER','aux_codes':'ALT-A','i_id':'STYLE'},
                                                   {'sku_id':'B','sku_code':'OTHER','i_id':'STYLE'}])
    def tearDown(self): self.f.close()
    def scan(self,code):
        return self.f.call('/api/item/scan',{'sid':self.f.sid,'batch_id':self.f.batch['id'],'code':code,'qty':0})
    def test_offline_hit_and_missing_do_not_network(self):
        r=self.scan('ALT-A'); self.assertTrue(r['ok'],r); self.assertEqual(r['item']['sku_id'],'A')
        self.assertTrue(self.scan('NOT-FOUND')['unmatched'])
        self.s.JST.uid=''; self.s.JST.coid=''; self.s.STORE.set_kv('catalog_company','WAREHOUSE')
        self.assertEqual(self.scan('ALT-A')['item']['sku_id'],'A')
    def test_ambiguous_barcode_prompts_candidates(self):
        r=self.scan('OTHER'); self.assertTrue(r['multi'],r)
        self.assertEqual({x['sku_id'] for x in r['candidates']},{'A','B'})
    def test_cross_company_no_hit(self):
        self.s.JST.coid='OTHER'
        self.assertTrue(self.scan('ALT-A')['unmatched'])
    def test_status_sync_and_cross_origin(self):
        r=self.f.call('/api/catalog/status'); self.assertTrue(r['ok']); self.assertEqual(r['rows'][0]['count'],2)
        self.assertTrue(self.f.call('/api/catalog/sync',{})['ok'])
        self.assertFalse(self.f.call('/api/catalog/sync',{},origin='https://foreign.invalid')['ok'])
        self.s.JST.uid=''
        self.assertFalse(self.f.call('/api/catalog/sync',{})['ok'])
    def test_online_fallback_explicit_and_cached(self):
        self.s.STORE.set_kv('catalog_online','1')
        calls=[]
        self.s.JST.lookup_item=lambda *args: calls.append(args) or {'sku_id':'A'}
        self.assertEqual(self.scan('NEW-CODE')['item']['sku_id'],'A')
        self.assertEqual(self.scan('NEW-CODE')['item']['sku_id'],'A')
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0],('NEW-CODE','OWNER','WAREHOUSE'))
    def test_refresh_between_preview_and_confirm_requires_rescan(self):
        self.assertEqual(self.scan('ALT-A')['item']['sku_id'],'A')
        self.s.CATALOG.replace(self.key,'货主',[{'sku_id':'B','aux_codes':'ALT-A'}])
        r=self.f.call('/api/item/scan',{'sid':self.f.sid,'batch_id':self.f.batch['id'],'code':'ALT-A',
                    'qty':1,'expected_sku_id':'A','expected_matched':True})
        self.assertFalse(r['ok'],r)
        self.assertIn('重新扫码',r['msg'])
        self.assertEqual(self.s.STORE.progress(self.f.sid)['recv'],10)

    def test_settings_persist_and_asset(self):
        self.assertTrue(self.f.call('/api/settings',{'catalog_auto':'0','catalog_online':'1'})['ok'])
        self.assertEqual(self.f.call('/api/settings')['catalog_auto'],'0')
        self.assertFalse(self.f.call('/api/catalog/sync',{})['ok'])


if __name__=='__main__': unittest.main(verbosity=2)
