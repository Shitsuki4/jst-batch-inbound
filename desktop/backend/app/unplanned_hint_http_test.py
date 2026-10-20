# -*- coding: utf-8 -*-
"""普通收货扫物流单号 → 「无采购单收货」已登记提示（离线 HTTP 回归，不联网）。

  · 桌面收货界面传 check_unplanned=true：命中未绑定登记时先返回 confirm_required，不建子批次；
    带 confirm_unplanned=true 再提交才建；
  · 同一采购单已绑定的登记作为提示返回；绑定其他采购单 / 已提交入库的登记不提示；
  · PDA / 原生 / 旧调用方不传参数：照常建子批次，只附带 unplanned_hint；
  · 无采购单库不存在或损坏：照常收货。
"""
import os
import unittest
from types import SimpleNamespace

from pda_test import Fixture as PdaFixture
from unplanned import UnplannedService

PO = '123'   # inbound_http_test.Fixture 建的采购单号


class HintFixture(PdaFixture):
    def __init__(self):
        super().__init__()
        self.server.DATA = self.temp.name   # server.DATA 只在首次 import 时读取环境变量
        self.unplanned_path = os.path.join(self.temp.name, 'unplanned.db')

    def seed(self, tracking, po_id='', state='active', submitted=False):
        svc = UnplannedService(self.unplanned_path, SimpleNamespace(coid='WAREHOUSE', uid='USER'),
                               lambda *a, **k: None, lambda *a, **k: [])
        try:
            st = svc.new('测试登记', tracking, owner='OWNER', auth='WAREHOUSE')
            sid, bid = st['session']['id'], st['current_batch']
            rev = svc.state(sid)['meta']['revision']
            svc.mutate('manual', dict(sid=sid, batch_id=bid, revision=rev, sku_id='A', name='测试商品 A',
                                      sku_code='', spec='', qty=4))
            with svc.store.db:
                if po_id:
                    svc.store.db.execute('UPDATE session SET po_id=? WHERE id=?', (po_id, sid))
                if state != 'active':
                    svc.store.db.execute('UPDATE session SET state=? WHERE id=?', (state, sid))
                if submitted:
                    svc.store.db.execute("INSERT INTO inbound_job(session_id,fingerprint,snapshot_json,login_co_id,status,created_at,updated_at) "
                                         "VALUES(?,?,?,?,?,?,?)", (sid, 'fp-%d' % sid, '{}', 'WAREHOUSE', 'complete', 'now', 'now'))
            return sid
        finally:
            svc.store.db.close()

    def batches(self):
        return [b['tracking_no'] for b in self.server.STORE.batches(self.sid)]

    def scan(self, no, **kw):
        return self.call('/api/tracking/scan', dict(sid=self.sid, tracking_no=no, **kw))


class DesktopHintTests(unittest.TestCase):
    def setUp(self): self.f = HintFixture()
    def tearDown(self): self.f.close()

    def test_no_unplanned_db_scans_normally(self):
        self.assertFalse(os.path.exists(self.f.unplanned_path))
        r = self.f.scan('SF-PLAIN-1', check_unplanned=True)
        self.assertTrue(r['ok'] and r['created'], r)
        self.assertNotIn('unplanned_hint', r)
        self.assertNotIn('confirm_required', r)
        self.assertFalse(os.path.exists(self.f.unplanned_path), '普通收货不应创建 unplanned.db')

    def test_unlinked_registration_requires_confirmation_before_batch(self):
        usid = self.f.seed('UP-HINT-1')
        before = self.f.batches()
        r = self.f.scan(' up-hint-1 ', check_unplanned=True)
        self.assertTrue(r['ok'], r)
        self.assertTrue(r.get('confirm_required'), r)
        self.assertNotIn('batch', r)
        hint = r['unplanned_hint']
        self.assertEqual(hint['kind'], 'unlinked')
        self.assertEqual(hint['record']['sid'], usid)
        self.assertEqual(hint['record']['qty'], 4)
        self.assertEqual(hint['open_url'], '/unplanned.html?sid=%d' % usid)
        self.assertIn('尚未绑定采购单', hint['message'])
        self.assertEqual(self.f.batches(), before, '去绑定 = 不确认：不能留下空子批次')
        r = self.f.scan(' up-hint-1 ', check_unplanned=True, confirm_unplanned=True)
        self.assertTrue(r['ok'] and r['created'], r)
        self.assertEqual(r['unplanned_hint']['kind'], 'unlinked')
        self.assertIn('up-hint-1', self.f.batches())
        # 已在本单建过的子批次再扫（补扫）不再提示
        r = self.f.scan('up-hint-1', check_unplanned=True)
        self.assertTrue(r['ok'] and not r['created'], r)
        self.assertNotIn('confirm_required', r)
        self.assertNotIn('unplanned_hint', r)

    def test_paused_session_not_resumed_until_confirmed(self):
        self.f.seed('UP-PAUSE-1')
        self.assertTrue(self.f.call('/api/session/pause', {'sid': self.f.sid})['ok'])
        r = self.f.scan('UP-PAUSE-1', check_unplanned=True)
        self.assertTrue(r.get('confirm_required'), r)
        self.assertEqual(self.f.server.STORE.get_session(self.f.sid)['state'], 'paused')
        r = self.f.scan('UP-PAUSE-1', check_unplanned=True, confirm_unplanned=True)
        self.assertTrue(r['ok'] and r['created'], r)
        self.assertEqual(self.f.server.STORE.get_session(self.f.sid)['state'], 'active')

    def test_same_po_is_reported_other_po_and_submitted_are_not(self):
        self.f.seed('UP-SAME-1', po_id=PO)
        self.f.seed('UP-OTHER-1', po_id='999')
        self.f.seed('UP-DONE-1', submitted=True)
        r = self.f.scan('UP-SAME-1', check_unplanned=True)
        self.assertTrue(r.get('confirm_required'), r)
        self.assertEqual(r['unplanned_hint']['kind'], 'same_po')
        self.assertIn('请勿重复收货', r['unplanned_hint']['message'])
        for no in ('UP-OTHER-1', 'UP-DONE-1'):
            r = self.f.scan(no, check_unplanned=True)
            self.assertTrue(r['ok'] and r['created'], r)
            self.assertNotIn('unplanned_hint', r)

    def test_callers_without_flag_get_batch_and_warning(self):
        # 原生桥 / 旧调用方：不传 check_unplanned → 照常建子批次，附带提示字段
        self.f.seed('UP-LEGACY-1')
        r = self.f.scan('UP-LEGACY-1')
        self.assertTrue(r['ok'] and r['created'], r)
        self.assertNotIn('confirm_required', r)
        self.assertEqual(r['unplanned_hint']['kind'], 'unlinked')

    def test_corrupt_unplanned_db_never_blocks(self):
        with open(self.f.unplanned_path, 'wb') as fh:
            fh.write(b'definitely not sqlite' * 200)
        r = self.f.scan('SF-CORRUPT-1', check_unplanned=True)
        self.assertTrue(r['ok'] and r['created'], r)
        self.assertNotIn('unplanned_hint', r)

    def test_pda_always_creates_batch_with_hint(self):
        self.f.seed('UP-PDA-1')
        self.f.pair()
        rid = 'd' * 32
        # 即使 PDA 请求带了 check_unplanned 也会被剥离：PDA 按请求编号记账，必须一次落库
        status, r, _ = self.f.http_call('/api/tracking/scan', {'sid': self.f.sid, 'tracking_no': 'UP-PDA-1',
                                                               'check_unplanned': True}, {'X-Request-ID': rid})
        self.assertEqual(status, 200)
        self.assertTrue(r['ok'] and r['created'], r)
        self.assertNotIn('confirm_required', r)
        self.assertEqual(r['unplanned_hint']['kind'], 'unlinked')
        self.assertIn('UP-PDA-1', self.f.batches())
        self.assertEqual(self.f.http_call('/api/pda/request?id=' + rid)[1]['response'], r)


if __name__ == '__main__':
    unittest.main(verbosity=2)
