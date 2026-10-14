# -*- coding: utf-8 -*-
import concurrent.futures
import os
import sys
import threading
import unittest
import urllib.request
from inbound_http_test import Fixture as BaseFixture
from inbound import InboundService
from inventory import InventoryService
from inventory_test import StockFake


class Fixture(BaseFixture):
    def __init__(self,port=0):
        super().__init__(port)
        s=self.server
        s.JST=StockFake()
        s.INBOUND=InboundService(s.STORE,s.JST)
        s.INVENTORY=InventoryService(s.STORE,s.JST)
        s.STORE.db.execute("UPDATE session SET owner_co_id='1200',auth_co_id='1500'")
        s.STORE.db.commit()
        self.sid=int(s.STORE.db.execute('SELECT id FROM session LIMIT 1').fetchone()[0])
    def preview(self,**kw):
        b={'owner':'1200','auth':'1500','wh_id':'1','code':'A','code_type':'sku',
           'pack_item_id':'11','expected_qty':'10','mode':'delta','value':'1'}
        b.update(kw);return self.call('/api/inventory/preview',b)


class HTTPTests(unittest.TestCase):
    def setUp(self):self.f=Fixture()
    def tearDown(self):self.f.close()
    def test_options_scope_and_assets(self):
        p=self.f.call('/api/inventory/partners');self.assertTrue(p['ok'],p)
        self.assertNotIn('2147483647',[r['id'] for r in p['owners']])
        c=self.f.call('/api/inventory/config?owner=1200&auth=1500');self.assertTrue(c['ok'],c)
        r=self.f.call('/api/inventory/lookup?owner=1200&auth=1500&wh_id=1&code=A');self.assertEqual(len(r['rows']),2)
        with urllib.request.urlopen(self.f.base+'/inventory.js') as r:self.assertEqual(r.status,200)
    def test_confirmation_and_origin(self):
        p=self.f.preview();self.assertTrue(p['ok'],p);b={'token':p['job']['token']}
        self.assertFalse(self.f.call('/api/inventory/submit',b)['ok'])
        b['confirmed']=True
        self.assertFalse(self.f.call('/api/inventory/submit',b,'https://unrelated.example')['ok'])
        j=self.f.call('/api/inventory/submit',b,self.f.base);self.assertEqual(j['job']['status'],'verified')
        self.assertEqual(len(self.f.server.JST.writes),1)
    def test_parallel_same_token_writes_once(self):
        p=self.f.preview();b={'token':p['job']['token'],'confirmed':True}
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            rows=list(pool.map(lambda _:self.f.call('/api/inventory/submit',b),range(4)))
        self.assertTrue(all(r['job']['status']=='verified' for r in rows));self.assertEqual(len(self.f.server.JST.writes),1)
    def test_unknown_visible_history_and_read_only_recovery(self):
        p=self.f.preview();self.f.server.JST.fail='after'
        b={'token':p['job']['token'],'confirmed':True}
        self.assertEqual(self.f.call('/api/inventory/submit',b)['job']['status'],'unknown')
        self.assertEqual(self.f.call('/api/inventory/history')['rows'][0]['status'],'unknown')
        self.assertEqual(self.f.call('/api/inventory/verify',b)['job']['status'],'verified')
        self.assertEqual(len(self.f.server.JST.writes),1)
    def test_stale_no_write_and_login_guard(self):
        p=self.f.preview();self.f.server.JST.stock[0]['qty']=15
        self.assertFalse(self.f.call('/api/inventory/submit',{'token':p['job']['token'],'confirmed':True})['ok'])
        self.assertEqual(len(self.f.server.JST.writes),0)
        self.f.server.JST.uid=''
        self.assertFalse(self.f.call('/api/inventory/history')['ok'])
        self.assertFalse(self.f.preview()['ok'])
    def test_inbound_warehouse_required_and_scope(self):
        p=self.f.call('/api/inbound/options?sid='+str(self.f.sid));self.assertTrue(p['ok'],p)
        self.assertEqual(p['owner'],'1200');self.assertEqual(p['auth'],'1500')
        p=self.f.call('/api/inbound/preview?sid=%s&wh_id=4'%self.f.sid)
        b={'sid':self.f.sid,'fingerprint':p['fingerprint'],'confirmed':True}
        self.assertFalse(self.f.call('/api/inbound/create',b)['ok'])
        b['wh_id']='4';j=self.f.call('/api/inbound/create',b);self.assertTrue(j['ok'],j)
        self.assertEqual(self.f.server.JST.docs[j['job']['io_id']]['head']['wh_id'],'4')

if __name__=='__main__':
    if '--serve' in sys.argv:
        f=Fixture(int(next((a.split('=',1)[1] for a in sys.argv if a.startswith('--port=')),'8899')))
        print(f.base,flush=True)
        try:threading.Event().wait()
        except KeyboardInterrupt:pass
        finally:f.close()
    else:unittest.main(verbosity=2)
