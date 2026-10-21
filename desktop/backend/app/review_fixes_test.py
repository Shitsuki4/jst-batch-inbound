# -*- coding: utf-8 -*-
"""2026-10-21 逻辑审查 P1 修复的回归（全程离线：FakeJst / StockFake + 临时库）。

覆盖：
  #2  PDA 请求不发电脑端录像事件（tracking / item），PDA 扫物流单号不抢电脑端当前单；电脑端行为不变
  #3  /api/pda/receipts：每次轮询只取一次货主范围、失败负缓存、ERP 调用不持全局锁、清单失败时降级可见
  #4  「应收」统一为本轮应收：差异表 / po_items / 远程页与 receiving_round 一致，采购总数另列
  #5  /api/session/del 删除正在录像的收货单时发 stop；删除无关历史单不打断录像
  #6  backup.VERSION 与应用版本同源，版本变化后会做升级前备份
  #9  远程页日期筛选：起止条件作用于同一活动时间（AND）
  #13 session/del 必须带 sid；batch/del 校验子批次归属
  硬约束：已结单的旧轮次仍可改数量、删子批次（不加只读限制）
"""
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault('JST_NATIVE_TOKEN', 'x' * 32)

from store import Store  # noqa: E402

_NATIVE = {'installed': False}


def install_native(server):
    """native_entry.py 在打包版里做的事。Handler 类只能包装一次（同一进程内复用）。"""
    import native_bridge
    if not _NATIVE['installed']:
        native_bridge.install(server)
        _NATIVE['installed'] = True
    else:
        native_bridge.migrate(server.STORE)


def events_after(store, mark):
    return [(r['kind'], json.loads(r['payload'])) for r in
            store.db.execute('SELECT kind,payload FROM native_event WHERE id>? ORDER BY id', (mark,))]


def read(path):
    with open(path, encoding='utf-8-sig') as fp:
        return fp.read()


def mark(store):
    return store.db.execute('SELECT COALESCE(MAX(id),0) FROM native_event').fetchone()[0]


# --------------------------------------------------------------------------- #2 / #3 PDA
class PdaTests(unittest.TestCase):
    def setUp(self):
        from pda_test import Fixture
        self.f = Fixture()
        self.s = self.f.server
        install_native(self.s)
        self.st = self.s.STORE

    def tearDown(self):
        self.f.close()

    def test_pda_tracking_and_item_emit_no_native_events_and_keep_desktop_session(self):
        f, st = self.f, self.st
        desk = f.sid
        items = [{'poi_id': '9', 'sku_id': 'A', 'sku_code': '690000000001', 'name': 'A', 'qty': 5}]
        pda_sid = st.open_session('PO-PDA', 'PO-PDA', 'po_id', {'owner_co_id': '1200', 'auth_co_id': '1500'}, items)
        st.activate_session(desk)                      # 电脑端正在收 desk，PDA 的单子挂起
        self.assertEqual(st.get_session(pda_sid)['state'], 'paused')
        f.pair()
        m = mark(st)
        status, r, _ = f.http_call('/api/tracking/scan', {'sid': pda_sid, 'tracking_no': 'PDA-SF-0001'},
                                   {'X-Request-ID': 'a' * 32})
        self.assertTrue(r.get('ok'), r)
        self.assertTrue(r.get('batch_id'))
        self.assertEqual(events_after(st, m), [], 'PDA 扫物流单号不得发电脑端录像事件')
        self.assertEqual(st.get_session(desk)['state'], 'active', 'PDA 不得挂起电脑端当前单')
        self.assertEqual(st.get_session(pda_sid)['state'], 'paused', 'PDA 只建子批次，不激活自己的单')
        m = mark(st)
        r = f.http_call('/api/item/scan', {'sid': f.sid, 'batch_id': f.batch['id'], 'code': 'A', 'qty': 1},
                        {'X-Request-ID': 'b' * 32})[1]
        self.assertTrue(r.get('ok'), r)
        self.assertEqual(events_after(st, m), [], 'PDA 商品扫码不得发 item 事件')

    def _storm(self, result):
        s = self.s
        calls = {'n': 0, 'lock_free': []}

        def fake():
            calls['n'] += 1
            got = []

            def probe():
                ok = s.state_lock.acquire(timeout=2)
                if ok:
                    s.state_lock.release()
                got.append(ok)
            t = threading.Thread(target=probe)
            t.start(); t.join()
            calls['lock_free'].append(bool(got and got[0]))
            return list(result)
        s.JST.partners = fake
        s.JST.wms_list = fake
        return calls

    def test_receipts_poll_negative_cache_outside_lock_and_degraded_list(self):
        f, s, st = self.f, self.s, self.st
        for i in range(9):
            st.open_session('PO-%d' % i, 'PO-%d' % i, 'po_id', {'owner_co_id': '1200', 'auth_co_id': '1500'},
                            [{'sku_id': 'A', 'qty': 1}])
        calls = self._storm([])                # ERP 返回空清单（或失败）
        s.invalidate_partners()                # 登录 / 切换账号
        f.pair()
        status, r, _ = f.http_call('/api/pda/receipts')
        self.assertTrue(r['ok'], r)
        self.assertLessEqual(calls['n'], 2, '一次轮询最多取一次清单（货主+分仓），不能按会话逐个取')
        self.assertTrue(all(calls['lock_free']), 'ERP 清单请求不得在持有 state_lock 时进行')
        self.assertEqual(len(r['rows']), 10, '清单失败时降级：仍列出收货单')
        self.assertTrue(all(x.get('scope_unverified') for x in r['rows']))
        self.assertIn('scope_warning', r)
        before = calls['n']
        for _ in range(3):
            f.http_call('/api/pda/receipts')
        self.assertEqual(calls['n'], before, '失败结果应短期缓存，2 秒轮询不能反复打 ERP')
        # 降级状态下禁止写入，并给出明确原因
        w = f.http_call('/api/tracking/scan', {'sid': f.sid, 'tracking_no': 'X-1'}, {'X-Request-ID': 'c' * 32})[1]
        self.assertFalse(w['ok'])
        self.assertIn('清单', w['msg'])
        # 登录 / 切换账号后负缓存立即失效，恢复正常
        calls2 = self._storm([{'id': '1200', 'name': '模拟货主'}])
        s.JST.wms_list = lambda: [{'id': '1500', 'name': '模拟仓库公司'}]
        s.invalidate_partners()
        r = f.http_call('/api/pda/receipts')[1]
        self.assertTrue(r['ok'], r)
        self.assertGreaterEqual(calls2['n'], 1)
        self.assertNotIn('scope_warning', r)
        self.assertEqual(len(r['rows']), 10)
        self.assertFalse(any(x.get('scope_unverified') for x in r['rows']))
        n = calls2['n']
        f.http_call('/api/pda/receipts')
        self.assertEqual(calls2['n'], n, '成功结果同样缓存')

    def test_receipt_scope_filters_foreign_owner(self):
        f, st = self.f, self.st
        st.open_session('PO-X', 'PO-X', 'po_id', {'owner_co_id': '9999', 'auth_co_id': '1500'}, [{'sku_id': 'A', 'qty': 1}])
        f.pair()
        r = f.http_call('/api/pda/receipts')[1]
        self.assertTrue(r['ok'], r)
        self.assertNotIn('PO-X', [x['po_id'] for x in r['rows']])
        self.assertIn(f.sid, [x['id'] for x in r['rows']])




# --------------------------------------------------------------------------- #4 应收口径 / #9 日期筛选
class RoundBasisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = Store(os.path.join(self.tmp.name, 'jst.db'))
        items = [{'poi_id': '1', 'sku_id': 'A', 'name': 'A', 'qty': 100, 'qty_known': True,
                  'in_qty': 60, 'in_qty_known': True}]
        self.sid = self.st.open_session('PO1', 'PO1', 'po_id', {'owner_co_id': 'O', 'auth_co_id': 'W'}, items)
        self.b, _ = self.st.ensure_batch(self.sid, 'SF1')
        self.st.add_qty(self.b['id'], self.sid, {'sku_id': 'A', 'name': 'A'}, 40)

    def tearDown(self):
        self.st.db.close()
        self.tmp.cleanup()

    def test_diff_po_items_remote_match_round(self):
        rnd = self.st.receiving_round(self.sid)['rows'][0]
        self.assertEqual((rnd['plan'], rnd['recv']), (40.0, 40.0))
        self.assertEqual(self.st.diff_report(self.sid), [], '本轮已收满，差异表不应显示「少收 60」')
        summary = self.st.po_received_summary(self.sid)[0]
        self.assertEqual((summary['plan'], summary['po_total'], summary['plan_basis'], summary['done']),
                         (40.0, 100.0, 'round', True))
        import server
        old = server.STORE
        server.STORE = self.st
        try:
            row = server.po_items(self.sid)[0]
        finally:
            server.STORE = old
        self.assertEqual((row['plan'], row['po_qty'], row['done'], row['plan_basis']), (40.0, 100.0, True, 'round'))
        from remote_access import ReadOnlyData
        rem = ReadOnlyData(self.tmp.name).detail('po', self.sid)['compare'][0]
        self.assertEqual((rem['plan'], rem['po_total'], rem['recv'], rem['status'], rem['plan_basis']),
                         (40.0, 100.0, 40.0, '一致', 'round'))
        # 少收时两边的差额一致
        self.st.add_qty(self.b['id'], self.sid, {'sku_id': 'A', 'name': 'A'}, -10)
        d = self.st.diff_report(self.sid)[0]
        self.assertEqual((d['plan'], d['recv'], d['reason'], d['po_total']), (40.0, 30.0, '少收 10', 100.0))
        self.assertEqual(self.st.receiving_round(self.sid)['rows'][0]['diff'], -10.0)

    def test_legacy_and_unknown_fall_back_to_total(self):
        self.st.db.execute('UPDATE session SET round_baseline_json=NULL WHERE id=?', (self.sid,))
        self.st.db.commit()
        d = self.st.diff_report(self.sid)[0]
        self.assertEqual((d['plan'], d['plan_basis'], d['reason']), (100.0, 'total', '少收 60'))
        from remote_access import ReadOnlyData
        rem = ReadOnlyData(self.tmp.name).detail('po', self.sid)['compare'][0]
        self.assertEqual((rem['plan'], rem['plan_basis']), (100.0, 'total'))
        # 数量未标 known 的条目（旧接口 / 测试替身）：按采购总数，不按零处理
        sid = self.st.open_session('PO2', 'PO2', 'po_id', {}, [{'poi_id': '1', 'sku_id': 'B', 'qty': 5}])
        row = self.st.po_received_summary(sid)[0]
        self.assertEqual((row['plan'], row['plan_basis']), (5.0, 'total'))

    def test_po_items_split_round_target_across_duplicate_lines(self):
        items = [{'poi_id': '1', 'sku_id': 'C', 'name': 'C', 'qty': 5, 'qty_known': True, 'in_qty': 0, 'in_qty_known': True},
                 {'poi_id': '2', 'sku_id': 'C', 'name': 'C', 'qty': 3, 'qty_known': True, 'in_qty': 4, 'in_qty_known': True}]
        sid = self.st.open_session('PO3', 'PO3', 'po_id', {}, items)
        b, _ = self.st.ensure_batch(sid, 'T3')
        self.st.add_qty(b['id'], sid, {'sku_id': 'C', 'name': 'C'}, 4)
        import server
        old = server.STORE
        server.STORE = self.st
        try:
            rows = server.po_items(sid)
        finally:
            server.STORE = old
        self.assertEqual(sum(r['plan'] for r in rows), 4.0, '各行本轮应收之和 = SKU 本轮应收（8-4）')
        self.assertTrue(all(r['sku_plan'] == 4.0 and r['sku_po_total'] == 8.0 for r in rows))
        self.assertEqual(sum(r['recv'] for r in rows), 4.0)
        self.assertTrue(all(r['done'] for r in rows), rows)
        self.assertEqual(self.st.diff_report(sid), [])

    def test_remote_date_filter_is_and(self):
        from remote_access import ReadOnlyData
        st = self.st
        st.db.execute("UPDATE session SET started_at='2026-10-01 09:00:00' WHERE id=?", (self.sid,))
        st.db.execute("UPDATE batch SET first_scan_at='2026-10-20 09:00:00' WHERE id=?", (self.b['id'],))
        st.db.commit()
        hist = lambda a, b: [r['id'] for r in ReadOnlyData(self.tmp.name).history('po', '', a, b)['rows']]
        self.assertEqual(hist('2026-10-10', '2026-10-12'), [], '范围内没有任何活动的单子不应出现')
        self.assertEqual(hist('2026-10-19', '2026-10-21'), [self.sid], '物流扫描落在范围内')
        self.assertEqual(hist('2026-09-30', '2026-10-01'), [self.sid], '开单时间落在范围内')
        self.assertEqual(hist('2026-10-15', ''), [self.sid], '只给开始日期')
        self.assertEqual(hist('', '2026-09-30'), [], '只给结束日期，且之前没有活动')
        self.assertEqual(hist('', '2026-10-01'), [self.sid])


# --------------------------------------------------------------------------- #5 / #13 删除接口
class DeleteTests(unittest.TestCase):
    def setUp(self):
        from inbound_http_test import Fixture
        self.f = Fixture()
        self.s = self.f.server
        install_native(self.s)
        self.st = self.s.STORE

    def tearDown(self):
        self.f.http.shutdown()

    def _new(self, po, state=None):
        sid = self.st.open_session(po, po, 'po_id', {'owner_co_id': 'OWNER', 'auth_co_id': 'WAREHOUSE'},
                                   [{'sku_id': 'A', 'qty': 1}])
        return sid

    def test_desktop_tracking_scan_unchanged(self):
        """电脑端（本机 HTTP）扫物流单号：仍激活挂起单并发 tracking 事件。"""
        f, st = self.f, self.st
        sid2 = self._new('PO2')
        st.activate_session(f.sid)
        m = mark(st)
        r = f.call('/api/tracking/scan', {'sid': sid2, 'tracking_no': 'DESK-1'})
        self.assertTrue(r['ok'], r)
        self.assertEqual([k for k, _ in events_after(st, m)], ['tracking'])
        self.assertEqual(st.get_session(sid2)['state'], 'active')
        self.assertEqual(st.get_session(f.sid)['state'], 'paused')

    def test_session_del_requires_sid(self):
        active = self.st.active_session()
        self.assertIsNotNone(active)
        r = self.f.call('/api/session/del', {})
        self.assertFalse(r['ok'])
        self.assertIsNotNone(self.st.get_session(active['id']), '不带 sid 不得删除当前收货单')
        self.assertFalse(self.f.call('/api/session/del', {'sid': 987654})['ok'])

    def test_session_del_stops_recording_of_deleted_session_only(self):
        f, st = self.f, self.st
        sid2 = self._new('PO2')                       # 新开的单为 active
        r = f.call('/api/tracking/scan', {'sid': sid2, 'tracking_no': 'REC-2'})
        self.assertTrue(r['ok'], r)
        m = mark(st)
        r = f.call('/api/session/del', {'sid': sid2})
        self.assertTrue(r['ok'], r)
        self.assertEqual([k for k, _ in events_after(st, m)], ['stop'], '删除正在录像的单必须停录')
        # 删除无关的挂起历史单：不打断另一张单的录像
        sid3 = self._new('PO3')
        sid4 = self._new('PO4')
        st.activate_session(sid4)                     # sid3 被挂起，sid4 为当前单
        r = f.call('/api/tracking/scan', {'sid': sid4, 'tracking_no': 'REC-4'})
        self.assertTrue(r['ok'], r)
        m = mark(st)
        r = f.call('/api/session/del', {'sid': sid3})
        self.assertTrue(r['ok'], r)
        self.assertEqual(events_after(st, m), [], '删除无关历史单不应停录')
        # 挂起但最近一次录像关联仍指向它（例如电脑端从挂起单扫码后又切走未停录）
        sid5 = self._new('PO5')
        st.activate_session(sid5)
        r = f.call('/api/tracking/scan', {'sid': sid5, 'tracking_no': 'REC-5'})
        self.assertTrue(r['ok'], r)
        st.pause_session(sid5)
        m = mark(st)
        r = f.call('/api/session/del', {'sid': sid5})
        self.assertTrue(r['ok'], r)
        self.assertEqual([k for k, _ in events_after(st, m)], ['stop'])

    def test_batch_del_checks_ownership(self):
        f, st = self.f, self.st
        other = self._new('PO-OTHER')
        st.activate_session(other)                    # other 为当前单，f.sid 挂起
        ob, _ = st.ensure_batch(other, 'OTHER-1')
        mine, _ = st.ensure_batch(f.sid, 'MINE-2')    # f.sid 已挂起
        r = f.call('/api/batch/del', {'batch_id': mine['id'], 'sid': other})
        self.assertFalse(r['ok'])
        r = f.call('/api/batch/del', {'batch_id': mine['id']})   # 未带 sid → 按当前单校验
        self.assertFalse(r['ok'])
        self.assertTrue(st.db.execute('SELECT 1 FROM batch WHERE id=?', (mine['id'],)).fetchone())
        r = f.call('/api/batch/del', {'batch_id': mine['id'], 'sid': f.sid})
        self.assertTrue(r['ok'], r)
        self.assertEqual([b['id'] for b in r['batches']], [x['id'] for x in st.batches(f.sid)],
                         '返回的是该子批次所属单的物流列表')
        r = f.call('/api/batch/del', {'batch_id': ob['id']})
        self.assertTrue(r['ok'], r)

    def test_closed_round_stays_editable(self):
        """硬约束：已结单的轮次仍可改数量、删子批次（同一采购单可多轮结单）。"""
        f, st = self.f, self.st
        b2, _ = st.ensure_batch(f.sid, 'CLOSED-2')
        lid = st.add_qty(b2['id'], f.sid, {'sku_id': 'A', 'name': '测试商品 A', 'poi_id': '1'}, 2)[0]
        st.close_session(f.sid)
        r = f.call('/api/line/set', {'line_id': lid, 'qty': 5})
        self.assertTrue(r['ok'], r)
        r = f.call('/api/batch/del', {'batch_id': b2['id'], 'sid': f.sid})
        self.assertTrue(r['ok'], r)
        self.assertEqual(st.get_session(f.sid)['state'], 'closed')


# --------------------------------------------------------------------------- #6 版本 / 备份
class VersionBackupTests(unittest.TestCase):
    def test_version_single_source(self):
        import app_version
        import backup
        self.assertEqual(backup.VERSION, app_version.VERSION)
        root = os.path.abspath(os.path.join(HERE, '..', '..'))
        csproj = read(os.path.join(root, 'src', 'Receiving.Native.csproj'))
        self.assertEqual(re.search(r'<Version>([^<]+)</Version>', csproj).group(1), app_version.VERSION,
                         'app_version.VERSION 必须与 csproj <Version> 一致')
        iss = read(os.path.join(root, 'installer', 'offline_setup.iss'))
        self.assertEqual(re.search(r'#define AppVersion "([^"]+)"', iss).group(1), app_version.VERSION)
        src = read(os.path.join(HERE, 'native_bridge.py'))
        self.assertNotIn("version='", src, '原生健康检查不得再硬编码版本号')

    def test_upgrade_makes_pre_upgrade_backup_once(self):
        import backup
        with tempfile.TemporaryDirectory() as d:
            Store(os.path.join(d, 'jst.db')).db.close()
            m = backup.BackupManager(d, threading.RLock())
            old = m.create('daily')
            # 模拟上一版本留下的备份（每日备份也会打版本标签）
            self.assertTrue(old['complete'])
            path = os.path.join(m.manifests()[0]['directory'], 'manifest.json')
            with open(path, encoding='utf-8') as fp:
                data = json.load(fp)
            data['version'] = '2.0.21-joint-receiving'
            with open(path, 'w', encoding='utf-8') as fp:
                json.dump(data, fp)
            time.sleep(0.01)
            n0 = len(m.manifests())
            m.before_upgrade()
            self.assertEqual(len(m.manifests()), n0 + 1, '版本变化后首次启动必须做升级前备份')
            self.assertEqual(m.manifests()[0]['reason'], 'pre-upgrade')
            self.assertEqual(m.manifests()[0]['version'], backup.VERSION)
            m.before_upgrade()
            self.assertEqual(len(m.manifests()), n0 + 1, '同一版本不重复做升级前备份')


if __name__ == '__main__':
    unittest.main(verbosity=2)
