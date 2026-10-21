# -*- coding: utf-8 -*-
"""HTTP 集成/并发回归，全程 FakeJst；--serve 用于本地浏览器验收。"""
import concurrent.futures
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from inbound import InboundService
from inbound_test import FakeJst
from store import Store


class Fixture:
    def __init__(self, port=0):
        self.temp = tempfile.TemporaryDirectory(prefix='jst-inbound-http-')
        os.environ['JST_DB_DIR'] = self.temp.name
        os.environ['JST_NO_BROWSER'] = '1'
        import server
        self.server = server
        server.STORE.db.close()
        server.STORE = Store(os.path.join(self.temp.name, 'fixture.db'))
        from catalog import CatalogDB, CatalogSync
        server.CATALOG = CatalogDB(os.path.join(self.temp.name, 'catalog.db'))
        server.CATALOG_SYNC = CatalogSync(server.CATALOG, server.catalog_provider)
        from learned_codes import LearnedCodes
        server.LEARNED = LearnedCodes(os.path.join(self.temp.name, 'learned_codes.db'))
        server.JST = FakeJst()
        server.JST.uname, server.JST.coname = '离线测试', '模拟仓库（不会写真实聚水潭）'
        server.JST.partners = lambda: [{'id': 'OWNER', 'name': '模拟货主'}]
        server.JST.wms_list = lambda: [{'id': 'WAREHOUSE', 'name': '模拟仓库'}]
        server.INBOUND = InboundService(server.STORE, server.JST)
        items = [{'poi_id': '1', 'sku_id': 'A', 'sku_code': '690000000001', 'name': '测试商品 A', 'qty': 100},
                 {'poi_id': '2', 'sku_id': 'B', 'sku_code': '690000000002', 'name': '测试商品 B', 'qty': 200}]
        self.sid = server.STORE.open_session('OUT-123', '123', 'po_id',
            {'owner_co_id': 'OWNER', 'auth_co_id': 'WAREHOUSE', 'supplier': '测试供应商'}, items)
        self.batch, _ = server.STORE.ensure_batch(self.sid, 'SF-TEST-001')
        self.lid = server.STORE.add_qty(self.batch['id'], self.sid,
            {'sku_id': 'A', 'name': '测试商品 A', 'poi_id': '1'}, 7)[0]
        server.STORE.add_qty(self.batch['id'], self.sid,
            {'sku_id': 'B', 'name': '测试商品 B', 'poi_id': '2'}, 3)
        self.http = server.DesktopHTTPServer(('127.0.0.1', port), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:%d' % self.http.server_address[1]

    def call(self, path, body=None, origin=None):
        headers = {'Content-Type': 'application/json'}
        if origin: headers['Origin'] = origin
        req = urllib.request.Request(self.base + path,
            data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as res: return json.load(res)
        except urllib.error.HTTPError as e:
            with e:
                return json.load(e)

    def close(self):
        self.http.shutdown(); self.http.server_close(); self.thread.join()
        self.server.STORE.db.close()
        self.temp.cleanup()


class HTTPTests(unittest.TestCase):
    def setUp(self): self.f = Fixture()
    def tearDown(self): self.f.close()

    def payload(self):
        r = self.f.call('/api/inbound/preview?sid=%s&wh_id=1' % self.f.sid)
        self.assertTrue(r['ok'])
        return {'sid': self.f.sid, 'wh_id': '1', 'fingerprint': r['fingerprint'], 'confirmed': True}

    def test_http_preview_confirm_repeat_and_history_lock(self):
        body = self.payload()
        self.assertFalse(self.f.call('/api/inbound/create', dict(body, confirmed=False))['ok'])
        self.assertFalse(self.f.call('/api/inbound/create', body, 'https://unrelated.example')['ok'])
        r = self.f.call('/api/inbound/create', body, self.f.base)
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['job']['io_id'], self.f.call('/api/inbound/create', body)['job']['io_id'])
        record = self.f.call('/api/session/detail?sid=%s' % self.f.sid)
        self.assertEqual(record['groups'][0]['inbound_io_id'], '8001')
        for path, payload in [('/api/line/set', {'line_id': self.f.lid, 'qty': 90}),
                              ('/api/line/del', {'line_id': self.f.lid}),
                              ('/api/batch/del', {'batch_id': self.f.batch['id']}),
                              ('/api/batch/rename', {'batch_id': self.f.batch['id'], 'tracking_no': 'changed'}),
                              ('/api/session/del', {'sid': self.f.sid})]:
            result = self.f.call(path, payload)
            self.assertFalse(result['ok'], path)
            self.assertIn('入库', result['msg'])
        preview = self.f.call('/api/inbound/preview?sid=%s' % self.f.sid)
        self.assertEqual(preview['snapshot']['items'], [])
        self.assertEqual(preview['jobs'][0]['status'], 'complete')

    def test_simultaneous_double_submit_only_one_remote_create(self):
        body = self.payload()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.f.call('/api/inbound/create', body), range(4)))
        self.assertTrue(all(r['ok'] for r in results), results)
        self.assertEqual(self.f.server.JST.creates, 1)

    def test_stale_confirmation_does_not_create(self):
        body = self.payload()
        self.f.call('/api/line/set', {'line_id': self.f.lid, 'qty': 15})
        r = self.f.call('/api/inbound/create', body)
        self.assertFalse(r['ok']); self.assertIn('变化', r['msg'])
        self.assertEqual(self.f.server.JST.creates, 0)

    def test_http_pending_retry_after_failure(self):
        self.f.server.JST.fail_qty = True
        r = self.f.call('/api/inbound/create', self.payload())
        self.assertFalse(r['ok'])
        job = self.f.call('/api/inbound/preview?sid=%s' % self.f.sid)['jobs'][0]
        r = self.f.call('/api/inbound/resume', {'job_id': job['id'], 'confirmed': True})
        self.assertTrue(r['ok'], r)
        self.assertEqual(self.f.server.JST.creates, 1)

    def test_closed_receipt_can_generate_and_get_assets(self):
        self.f.server.STORE.close_session(self.f.sid)
        self.assertTrue(self.f.call('/api/inbound/create', self.payload())['ok'])
        for path in ['/', '/app.js', '/inbound.js', '/style.css']:
            with urllib.request.urlopen(self.f.base + path) as r:
                self.assertEqual(r.status, 200)
                self.assertTrue(r.read())



    def test_missing_product_can_edit_to_valid_sku_then_generate(self):
        s=self.f.server;s.STORE.set_kv('inbound_validate_purchase','0')
        lid,_=s.STORE.add_qty(self.f.batch['id'],self.f.sid,dict(key='RAW:MISSING',sku_id='MISSING',name='手工未知',matched=False),2)
        p=self.f.call('/api/inbound/preview?sid=%s&wh_id=3'%self.f.sid)
        body=dict(sid=self.f.sid,wh_id='3',fingerprint=p['fingerprint'],confirmed=True)
        r=self.f.call('/api/inbound/create',body);self.assertFalse(r['ok']);self.assertIn('MISSING',r['msg']);self.assertEqual(s.JST.creates,0)
        line=next(l for l in s.STORE.batch_lines(self.f.batch['id']) if l['id']==lid)
        values={k:line[k] or '' for k in ('sku_id','name','sku_code','spec')};values.update(sku_id='X',name='人工填写X')
        r=self.f.call('/api/line/edit',dict(line_id=lid,sid=self.f.sid,batch_id=self.f.batch['id'],expected=line,values=values))
        self.assertTrue(r['ok'],r);self.assertEqual(r['line']['qty'],2)
        self.assertFalse(self.f.call('/api/inbound/create',body)['ok']);self.assertEqual(s.JST.creates,0)
        p=self.f.call('/api/inbound/preview?sid=%s&wh_id=3'%self.f.sid);body['fingerprint']=p['fingerprint']
        r=self.f.call('/api/inbound/create',body);self.assertTrue(r['ok'],r)
        self.assertEqual(s.JST.creates,1)
        self.assertEqual({x['sku_id']:float(x['qty']) for x in s.JST.docs[r['job']['io_id']]['rows']},{'A':7,'B':3,'X':2})

    def test_setting_http_persistence_and_external_goods_end_to_end(self):
        self.assertEqual(self.f.call('/api/settings')['inbound_validate_purchase'], '1')
        self.f.server.STORE.add_qty(self.f.batch['id'], self.f.sid,
            {'sku_id': 'X', 'name': '采购单外商品', 'matched': False}, 4)
        self.assertFalse(self.f.call('/api/inbound/preview?sid=%s&wh_id=4' % self.f.sid)['ok'])
        self.assertTrue(self.f.call('/api/settings', {'inbound_validate_purchase': '0'})['ok'])
        self.assertEqual(self.f.call('/api/settings')['inbound_validate_purchase'], '0')
        preview = self.f.call('/api/inbound/preview?sid=%s&wh_id=4' % self.f.sid)
        self.assertTrue(preview['ok'], preview)
        self.assertFalse(preview['snapshot']['validate_purchase_details'])
        result = self.f.call('/api/inbound/create', {'sid': self.f.sid, 'wh_id': '4',
            'fingerprint': preview['fingerprint'], 'confirmed': True})
        self.assertTrue(result['ok'], result)
        doc = self.f.server.JST.docs[result['job']['io_id']]
        self.assertEqual(doc['head']['wh_id'], '4')
        self.assertEqual({p['sku_id']: float(p['qty']) for p in doc['rows']}, {'A': 7, 'B': 3, 'X': 4})

    def test_invalid_setting_rejected_without_partial_save_and_cross_site_denied(self):
        for value in ['false', 'true', '2', '', None, False]:
            self.assertFalse(self.f.call('/api/settings', {'inbound_validate_purchase': value, 'sound': '0'})['ok'])
            self.assertEqual(self.f.call('/api/settings')['inbound_validate_purchase'], '1')
            self.assertEqual(self.f.call('/api/settings')['sound'], '1')
        self.assertFalse(self.f.call('/api/settings', {'inbound_validate_purchase': '0'}, 'https://evil.example')['ok'])
        self.assertEqual(self.f.call('/api/settings')['inbound_validate_purchase'], '1')

    def test_setting_http_changes_require_reconfirmation_and_default_check_remains(self):
        body = self.payload()
        self.f.call('/api/settings', {'inbound_validate_purchase': '0'})
        result = self.f.call('/api/inbound/create', body)
        self.assertFalse(result['ok'])
        self.assertIn('已变化', result['msg'])
        self.assertEqual(self.f.server.JST.creates, 0)
        self.f.server.JST.detail_rows = []
        self.f.call('/api/settings', {'inbound_validate_purchase': '1'})
        self.assertFalse(self.f.call('/api/inbound/create', self.payload())['ok'])
        self.assertEqual(self.f.server.JST.creates, 0)

    def test_http_missing_product_returns_named_block_and_no_empty_draft(self):
        self.f.call('/api/settings',{'inbound_validate_purchase':'0'})
        del self.f.server.JST.products['B']
        r=self.f.call('/api/inbound/create',self.payload())
        self.assertFalse(r['ok']);self.assertIn('商品资料中不存在',r['msg']);self.assertIn('B',r['msg'])
        self.assertEqual(self.f.server.JST.creates,0)
        self.assertEqual(self.f.server.STORE.inbound_jobs(self.f.sid),[])
        self.assertTrue(self.f.call('/api/line/set',{'line_id':self.f.lid,'qty':8})['ok'])

    def test_http_product_lookup_timeout_preserves_receipt_for_retry(self):
        self.f.server.JST.product_error=True
        r=self.f.call('/api/inbound/create',self.payload())
        self.assertFalse(r['ok']);self.assertIn('商品资料校验失败',r['msg'])
        self.assertEqual(self.f.server.JST.creates,0)
        self.f.server.JST.product_error=False
        self.assertTrue(self.f.call('/api/inbound/create',self.payload())['ok'])


if __name__ == '__main__':
    if '--serve' in sys.argv:
        f = Fixture(int(next((a.split('=', 1)[1] for a in sys.argv if a.startswith('--port=')), '8898')))
        print(f.base, flush=True)
        try: threading.Event().wait()
        except KeyboardInterrupt: pass
        finally: f.close()
    else:
        unittest.main(verbosity=2)
