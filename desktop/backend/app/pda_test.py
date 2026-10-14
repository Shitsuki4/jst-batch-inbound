"""Offline PDA/backup regression. All inventory and inbound writes use StockFake."""
from contextlib import closing
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import ssl
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from inventory_http_test import Fixture as BaseFixture
from operations import Operations
from backup import BackupManager
from pda import PdaHost

class Fixture(BaseFixture):
    def __init__(self, port=0, host='127.0.0.1'):
        super().__init__(port)
        s=self.server
        s.DATA=self.temp.name
        s.OPERATIONS=Operations(s.STORE,s.JST,s.INVENTORY)
        s.BACKUPS=BackupManager(self.temp.name,s.state_lock)
        s.LAN=PdaHost(s)
        self.host=s.LAN
        sock=socket.socket();sock.bind((host,0));port=sock.getsockname()[1];sock.close()
        self.host.start(host,port)
        self.url=self.host.status()['url']
        self.ctx=ssl._create_unverified_context()  # test-only self-signed cert, APK uses SHA256 pinning
        self.cookie=''
    def http_call(self,path,body=None,headers=None):
        h={'Content-Type':'application/json','Cookie':self.cookie,'Origin':self.url}
        h.update(headers or {})
        req=urllib.request.Request(self.url+path,data=json.dumps(body).encode() if body is not None else None,headers=h)
        try:
            res=urllib.request.urlopen(req,context=self.ctx,timeout=15)
        except urllib.error.HTTPError as e:
            res=e
        with res:
            content=res.read()
            try: result=json.loads(content)
            except ValueError: result=content.decode('utf-8')
            return res.status,result,dict(res.headers)
    def pair(self):
        p=self.host.pairing()
        status,r,h=self.http_call('/api/pda/pair',{'code':p['code'],'name':'PDA-test'})
        assert status==200 and r['ok'],r
        self.cookie=h['Set-Cookie'].split(';')[0]
        return p
    def close(self):
        self.host.stop()
        super().close()

class BackupTests(unittest.TestCase):
    def test_online_wal_and_no_secrets(self):
        with tempfile.TemporaryDirectory() as d:
            db=sqlite3.connect(Path(d)/'jst.db');db.execute('PRAGMA journal_mode=WAL');db.execute('CREATE TABLE t(x)');db.execute('INSERT INTO t VALUES(7)');db.commit()
            (Path(d)/'login-secret.txt').write_text('not-in-backup')
            b=BackupManager(d,threading.RLock());m=b.create();self.assertTrue(m['complete'])
            directory=Path(b.status()['latest']['directory']);self.assertEqual({p.name for p in directory.iterdir()},{'manifest.json','jst.db'})
            with closing(sqlite3.connect(directory/'jst.db')) as copy:self.assertEqual(copy.execute('SELECT x FROM t').fetchone()[0],7)
            b.daily();self.assertEqual(b.status()['count'],1)
            db.close()
    def test_failed_preupgrade_does_not_mark_success(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'jst.db').write_bytes(b'not a db')
            b=BackupManager(d,threading.RLock())
            with self.assertRaises(sqlite3.DatabaseError):b.before_upgrade()
            self.assertFalse(b.status()['latest']['complete'])
            with self.assertRaises(sqlite3.DatabaseError):b.before_upgrade()

class PdaTests(unittest.TestCase):
    def setUp(self):self.f=Fixture();self.f.pair()
    def tearDown(self):self.f.close()
    def test_pairing_expiry_one_use_cookie_and_revoke(self):
        f=self.f;p=f.host.pairing();pcode=p['code'];f.host.pair_until=time.time()-1
        self.assertFalse(f.http_call('/api/pda/pair',{'code':pcode,'name':'x'})[1]['ok'])
        p=f.pair();self.assertFalse(f.http_call('/api/pda/pair',{'code':p['code'],'name':'x'})[1]['ok'])
        self.assertTrue(f.http_call('/api/pda/info')[1]['ok'])
        d=f.host.authorize(f.cookie);f.host.revoke(d['id']);self.assertEqual(f.http_call('/api/pda/info')[0],401)
    def test_scope_and_admin_block(self):
        f=self.f
        for path in ('/api/settings','/api/accounts','/api/state','/api/backup/status','/api/pda/status','/index.html','/server.py','/api/debug/wms-raw'):
            self.assertEqual(f.http_call(path)[0],403,path)
        for path in ('/api/logout','/api/settings','/api/open/folder','/api/session/del','/api/pda/start'):
            self.assertEqual(f.http_call(path,{})[0],403,path)
        self.assertEqual(f.http_call('/api/inventory/preview',{}, {'Origin':'https://evil.example'})[0],403)
        self.assertEqual(f.http_call('/api/pda/info',headers={'Host':'evil.example'})[0],403)
        self.assertEqual(f.http_call('/api/pda/info',headers={'Cookie':''})[0],401)
        self.assertEqual(len(f.http_call('/api/pda/receipts')[1]['rows']),1)
        self.assertEqual(f.http_call('/api/session/detail?sid='+str(f.sid))[1]['session']['auth_co_id'],'1500')
        f.server.STORE.db.execute("UPDATE session SET owner_co_id='2147483647'");f.server.STORE.db.commit()
        self.assertEqual(f.http_call('/api/pda/receipts')[1]['rows'],[])
        self.assertFalse(f.http_call('/api/session/detail?sid='+str(f.sid))[1]['ok'])
    def test_desktop_host_and_cross_site_guards(self):
        f=self.f
        for path,body,headers in [('/api/state',None,{'Host':'evil.example'}),
                                   ('/api/settings',{}, {'Origin':'https://evil.example'}),
                                   ('/api/state',None,{'Sec-Fetch-Site':'cross-site'})]:
            req=urllib.request.Request(f.base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
            with self.assertRaises(urllib.error.HTTPError) as err: urllib.request.urlopen(req,timeout=5)
            self.assertEqual(err.exception.code,403)
        rows=[(socket.AF_INET,None,None,None,(ip,0)) for ip in ('192.168.1.2','10.1.2.3','172.31.1.1','198.18.0.1','8.8.8.8','127.0.0.1')]
        with patch('socket.getaddrinfo',return_value=rows):
            self.assertEqual(f.host.addresses(),['10.1.2.3','172.31.1.1','192.168.1.2'])

    def test_device_identity_login_invalidates(self):
        f=self.f;f.server.JST.uid='999';self.assertEqual(f.http_call('/api/pda/info')[0],401)
    def test_receipt_repeat_and_result_query(self):
        f=self.f;b={'sid':f.sid,'batch_id':f.batch['id'],'code':'A','qty':2};rid='a'*32
        self.assertFalse(f.http_call('/api/item/scan',b)[1]['ok'])
        r=f.http_call('/api/item/scan',b,{'X-Request-ID':rid})[1];self.assertTrue(r['ok'],r)
        again=f.http_call('/api/item/scan',b,{'X-Request-ID':rid})[1];self.assertEqual(r,again)
        line=f.server.STORE.batch_lines(f.batch['id'])[0];self.assertEqual(line['qty'],9)
        self.assertEqual(f.http_call('/api/pda/request?id='+rid)[1]['response'],r)
        self.assertFalse(f.http_call('/api/item/scan',dict(b,qty=3),{'X-Request-ID':rid})[1]['ok'])
    def test_simultaneous_receipt_requests_once(self):
        f=self.f;b={'sid':f.sid,'batch_id':f.batch['id'],'code':'A','qty':1}
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            rs=list(pool.map(lambda _:f.http_call('/api/item/scan',b,{'X-Request-ID':'b'*32})[1],range(3)))
        self.assertTrue(all(r['ok'] for r in rs),rs)
        self.assertEqual(f.server.STORE.batch_lines(f.batch['id'])[0]['qty'],8)
    def test_reserved_crash_never_replays(self):
        f=self.f;b={'sid':f.sid,'batch_id':f.batch['id'],'code':'A','qty':1};rid='c'*32
        f.server.OPERATIONS.reserve(rid,'/api/item/scan',b,'test')
        f.server.OPERATIONS=Operations(f.server.STORE,f.server.JST,f.server.INVENTORY)
        self.assertFalse(f.http_call('/api/item/scan',b,{'X-Request-ID':rid})[1]['ok'])
        self.assertEqual(f.http_call('/api/pda/request?id='+rid)[1]['status'],'unknown')
        self.assertEqual(f.server.STORE.batch_lines(f.batch['id'])[0]['qty'],7)
    def test_cross_batch_nan_and_foreign_candidate_rejected(self):
        f=self.f;b={'sid':f.sid,'batch_id':f.batch['id'],'code':'A','qty':1}
        for override in ({'batch_id':999},{'qty':float('inf')},{'qty':-1},{'qty':True}):
            self.assertFalse(f.http_call('/api/item/scan',dict(b,**override),{'X-Request-ID':'d'*32})[1]['ok'])
        self.assertFalse(f.http_call('/api/item/choose',dict(b,item={'sku_id':'EVIL','poi_id':'999'}),{'X-Request-ID':'e'*32})[1]['ok'])
    def test_inventory_actor_unknown_notes_and_csv(self):
        f=self.f;b={'owner':'1200','auth':'1500','wh_id':'1','code':'A','code_type':'sku','pack_item_id':'11','expected_qty':'10','mode':'delta','value':'1','actor':'spoofed'}
        p=f.http_call('/api/inventory/preview',b)[1];self.assertTrue(p['ok'],p);token=p['job']['token']
        self.assertIn('PDA:PDA-test',p['job']['snapshot']['actor'])
        f.server.JST.fail='after';j=f.http_call('/api/inventory/submit',{'token':token,'confirmed':True})[1];self.assertEqual(j['job']['status'],'unknown')
        n=f.http_call('/api/operations/note',{'kind':'inventory','ref':token,'text':'=HYPERLINK("x")'})[1];self.assertTrue(n['ok'],n)
        self.assertEqual(f.server.INVENTORY.get(token)['status'],'unknown')
        # Every CSV field is neutralized, including leading whitespace before formulas.
        with patch.object(f.server.OPERATIONS,'rows',return_value=[{'kind':'inventory','title':'  =danger','notes':[]}]):
            self.assertIn("'  =danger",f.server.OPERATIONS.export().decode('utf-8'))
        j=f.http_call('/api/inventory/verify',{'token':token})[1];self.assertEqual(j['job']['status'],'verified');self.assertEqual(len(f.server.JST.writes),1)
    def test_assets_security_and_body_limit(self):
        f=self.f;code,body,h=f.http_call('/');self.assertEqual(code,200);self.assertIn('PDA',body);self.assertIn("frame-ancestors 'none'",h['Content-Security-Policy'])
        r=f.http_call('/api/inventory/preview',{'large':'x'*70000})[1];self.assertFalse(r['ok'])
    def test_inbound_selected_warehouse_no_confirm(self):
        f=self.f;p=f.http_call('/api/inbound/preview?sid=%d&wh_id=4'%f.sid)[1]
        b={'sid':f.sid,'wh_id':'4','fingerprint':p['fingerprint'],'confirmed':True}
        r=f.http_call('/api/inbound/create',b)[1];self.assertTrue(r['ok'],r)
        self.assertEqual(f.server.JST.docs[r['job']['io_id']]['head']['wh_id'],'4')
        self.assertEqual(f.server.JST.creates,1)
        self.assertNotIn('ConfirmBatch',[c[1] for c in f.server.JST.calls])

    def test_inbound_external_goods_use_host_setting_but_pda_cannot_change_it(self):
        f = self.f
        f.server.STORE.add_qty(f.batch['id'], f.sid,
            {'sku_id': 'X', 'name': '采购单外测试商品', 'matched': False}, 4)
        path = '/api/inbound/preview?sid=%d&wh_id=4' % f.sid
        self.assertFalse(f.http_call(path)[1]['ok'])
        status, denied, _ = f.http_call('/api/settings', {'inbound_validate_purchase': '0'})
        self.assertEqual(status, 403)
        self.assertEqual(f.server.STORE.get_kv('inbound_validate_purchase', '1'), '1')
        self.assertTrue(f.call('/api/settings', {'inbound_validate_purchase': '0'})['ok'])
        preview = f.http_call(path)[1]
        self.assertTrue(preview['ok'], preview)
        self.assertFalse(preview['snapshot']['validate_purchase_details'])
        body = {'sid': f.sid, 'wh_id': '4', 'fingerprint': preview['fingerprint'], 'confirmed': True}
        result = f.http_call('/api/inbound/create', body)[1]
        self.assertTrue(result['ok'], result)
        rows = f.server.JST.docs[result['job']['io_id']]['rows']
        self.assertEqual(next(float(r['qty']) for r in rows if r['sku_id'] == 'X'), 4)
        self.assertFalse(f.http_call(path)[1]['jobs'][0]['validate_purchase_details'])
        self.assertEqual(f.http_call('/api/inbound/create', body)[1]['job']['id'], result['job']['id'])
        self.assertEqual(f.server.JST.creates, 1)

if __name__=='__main__':
    if '--serve' in sys.argv:
        f=Fixture(8899)
        p=f.host.pairing()
        info={'desktop':f.base,'url':f.url,'pin':f.host.pin,'code':p['code'],'sid':f.sid,'batch':f.batch['id']}
        print(json.dumps(info),flush=True)
        # Only fake pairing metadata is written for browser/Android integration tests.
        target=next((x.split('=',1)[1] for x in sys.argv if x.startswith('--info=')),None)
        if target:Path(target).write_text(json.dumps(info),encoding='utf-8')
        try:threading.Event().wait()
        except KeyboardInterrupt:pass
        finally:f.close()
    else:unittest.main(verbosity=2)
