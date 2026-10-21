# -*- coding: utf-8 -*-
"""操作记录（operation_log.db）离线回归：全程 FakeJst / StockFake，不接触真实聚水潭。"""
import os
import sqlite3
import unittest

from oplog import OpLog
from pda_test import Fixture as PdaFixture
from server import LoginError

SECRET_PASSWORD = 'Pw-Secret-9b1c7e'


class Fixture(PdaFixture):
    def __init__(self, port=0):
        super().__init__(port)
        self.db_path = os.path.join(self.temp.name, 'operation_log.db')
        self.server.OPLOG = OpLog(lambda: self.db_path, lambda: '180')
        self.oplog = self.server.OPLOG

    def settle(self):
        # 记录在响应发出后、仍持有 state_lock 时写入；拿一次锁即可等它写完
        with self.server.state_lock:
            pass

    def rows(self, **f):
        self.settle()
        return self.oplog.query(f, 1000)['rows']

    def raw_text(self):
        self.settle()
        if not os.path.exists(self.db_path):
            return ''
        db = sqlite3.connect(self.db_path)
        try:
            return '\n'.join(repr(tuple(r)) for r in db.execute('SELECT * FROM op_log'))
        finally:
            db.close()


class OpLogTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()

    def tearDown(self):
        self.f.close()

    def scan_desktop(self, qty=2):
        r = self.f.call('/api/item/scan', {'sid': self.f.sid, 'batch_id': self.f.batch['id'], 'code': 'A', 'qty': qty})
        self.assertTrue(r['ok'], r)
        return r

    def test_key_actions_record_before_after_qty(self):
        f = self.f
        self.scan_desktop(2)  # A: 7 -> 9
        line = [l for l in f.server.STORE.batch_lines(f.batch['id']) if l['sku_id'] == 'A'][0]
        r = f.call('/api/line/set', {'sid': f.sid, 'batch_id': f.batch['id'], 'line_id': line['id'], 'qty': 4})
        self.assertTrue(r['ok'], r)
        r = f.call('/api/batch/rename', {'sid': f.sid, 'batch_id': f.batch['id'], 'tracking_no': 'SF-RENAMED-9'})
        self.assertTrue(r['ok'], r)
        rows = f.rows()
        scan = [x for x in rows if x['op'] == 'item_scan']
        self.assertTrue(scan, rows)
        self.assertEqual((scan[0]['qty_before'], scan[0]['qty_after']), (7, 9))
        self.assertEqual(scan[0]['device_kind'], 'desktop')
        edit = [x for x in rows if x['qty_before'] == 9 and x['qty_after'] == 4]
        self.assertTrue(edit, rows)
        ren = [x for x in rows if x['op'] == 'batch_rename']
        self.assertTrue(ren and 'SF-RENAMED-9' in ren[0]['summary'], rows)
        # 「查看此单操作记录」：source=receipt + sid 必须能查到全部本单记录
        scoped = f.oplog.query({'source': 'receipt', 'sid': f.sid}, 1000)
        self.assertGreaterEqual(scoped['total'], 3)
        self.assertTrue(all(x['sid'] == f.sid for x in scoped['rows']))
        # 来源为空但 sid 相同的记录也归入本单
        f.oplog.write([{'module': 'purchase_watch', 'op': 'watch', 'source': '', 'sid': f.sid, 'summary': '旧记录'}])
        self.assertEqual(f.oplog.query({'source': 'receipt', 'sid': f.sid}, 1000)['total'], scoped['total'] + 1)
        # 服务端分页：offset
        f.settle()
        p1 = f.call('/api/oplog/list?limit=1&offset=0')
        p2 = f.call('/api/oplog/list?limit=1&offset=1')
        self.assertTrue(p1['ok'] and p2['ok'])
        self.assertEqual(p1['total'], p2['total'])
        self.assertEqual(p2['offset'], 1)
        self.assertNotEqual(p1['rows'][0]['id'], p2['rows'][0]['id'])

    def test_pda_request_attributed_to_device(self):
        f = self.f
        f.pair()
        b = {'sid': f.sid, 'batch_id': f.batch['id'], 'code': 'A', 'qty': 3}
        r = f.http_call('/api/item/scan', b, {'X-Request-ID': 'c' * 32})[1]
        self.assertTrue(r['ok'], r)
        scan = [x for x in f.rows() if x['op'] == 'item_scan']
        self.assertTrue(scan)
        self.assertEqual(scan[0]['device_kind'], 'pda')
        self.assertEqual(scan[0]['device_name'], 'PDA-test')
        self.assertTrue(scan[0]['device_id'])
        self.assertEqual((scan[0]['qty_before'], scan[0]['qty_after']), (7, 10))

    def test_no_password_cookie_or_token_stored(self):
        f = self.f

        def bad_login(*a, **kw):
            raise LoginError('账号或密码错误')
        f.server.JST.login = bad_login
        r = f.call('/api/login', {'account': 'tester', 'password': SECRET_PASSWORD, 'verify_code': '1234'})
        self.assertFalse(r['ok'])
        p = f.pair()
        cookie = f.cookie.split('=', 1)[1]
        r = f.http_call('/api/item/scan', {'sid': f.sid, 'batch_id': f.batch['id'], 'code': 'A', 'qty': 1,
                                            'token': 'tok-should-not-store', 'password': SECRET_PASSWORD},
                        {'X-Request-ID': 'd' * 32})[1]
        self.assertTrue(r['ok'], r)
        f.oplog.write([{'op': 'note', 'detail': {'password': SECRET_PASSWORD, 'cookie': cookie,
                                                 'access_token': 'tok-should-not-store'}}])
        logins = [x for x in f.rows() if x['op'] == 'login' and x['result'] == 'fail']
        self.assertTrue(logins, f.rows())
        text = f.raw_text()
        self.assertTrue(text)
        for secret in (SECRET_PASSWORD, cookie, p['code'], 'tok-should-not-store'):
            self.assertNotIn(secret, text)

    def test_oplog_failure_does_not_break_business(self):
        f = self.f
        f.oplog.disabled_for_test = True
        before = f.oplog.failures
        self.scan_desktop(5)
        line = [l for l in f.server.STORE.batch_lines(f.batch['id']) if l['sku_id'] == 'A'][0]
        self.assertEqual(line['qty'], 12)
        f.settle()
        self.assertGreater(f.oplog.failures, before)
        f.oplog.disabled_for_test = False
        self.assertEqual(f.rows(), [])
        self.assertTrue(f.call('/api/oplog/list')['ok'])

    def test_inbound_failure_recorded(self):
        f = self.f
        p = f.call('/api/inbound/preview?sid=%s&wh_id=1' % f.sid)
        self.assertTrue(p['ok'], p)
        f.server.JST.fail_qty = True
        r = f.call('/api/inbound/create', {'sid': f.sid, 'wh_id': '1', 'fingerprint': p['fingerprint'], 'confirmed': True})
        self.assertFalse(r['ok'])
        rows = f.rows(module='inbound')
        ops = {x['op']: x for x in rows}
        self.assertIn('inbound_request', ops)
        self.assertIn('inbound_result', ops)
        self.assertEqual(ops['inbound_result']['result'], 'fail')
        self.assertEqual(ops['inbound_result']['source'], 'receipt')
        self.assertEqual(ops['inbound_result']['sid'], f.sid)


if __name__ == '__main__':
    unittest.main(verbosity=2)
