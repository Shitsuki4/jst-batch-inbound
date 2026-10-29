# -*- coding: utf-8 -*-
"""快递单登记的物流单号在各个查询入口都能搜到：

- 记录查询（/api/receipts/extra-history）：按完整单号、部分单号、小写都能搜到，结果里列出命中的单号；
  其他来源（例如联合收货）查询出错时快递单登记的结果照样显示。
- 录像查询（电脑端，/api/native/registrations）：四种收货来源的登记都能按单号查到，
  key 与录像片段 BatchKey 一致。
- 升级前已经登记的单号（express.db 里的旧数据）不用迁移就能搜到，且搜索不改数据。
远程管理页（收货历史 / 录像查询）见 remote_access_test.py。
"""
import hashlib
import os
import sqlite3
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('JST_NATIVE_TOKEN', 'tracking-search-test')

from inbound_http_test import Fixture  # noqa: E402
from store import Store  # noqa: E402
import tracking_search  # noqa: E402


def _digest(path):
    db = sqlite3.connect(path)
    try:
        rows = []
        for sql in ('SELECT id,input_no,po_id,state,started_at,ended_at FROM session ORDER BY id',
                    'SELECT id,session_id,tracking_no FROM batch ORDER BY id',
                    'SELECT id,batch_id,sku_id,qty FROM line ORDER BY id',
                    'SELECT sid,note FROM unplanned_meta ORDER BY sid'):
            rows.append(repr(db.execute(sql).fetchall()))
        return hashlib.sha256('\n'.join(rows).encode('utf-8')).hexdigest()
    finally:
        db.close()


class TrackingSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = Fixture()
        cls.s = cls.f.server
        import native_bridge
        cls.nb = native_bridge
        native_bridge.install(cls.s)
        # 升级前已有的快递单登记：直接写在 express.db 里（还没有任何服务打开过它）
        path = os.path.join(cls.s.DATA, 'express.db')
        assert not os.path.exists(path)
        old = Store(path)
        old.db.execute('CREATE TABLE IF NOT EXISTS unplanned_meta(sid INTEGER PRIMARY KEY, note TEXT NOT NULL DEFAULT "", '
                       'revision INTEGER NOT NULL DEFAULT 0, checked_revision INTEGER NOT NULL DEFAULT -1, '
                       'checked_at TEXT NOT NULL DEFAULT "", purchase_digest TEXT NOT NULL DEFAULT "")')
        c = old.db.execute("INSERT INTO session(input_no,po_id,id_type,state,started_at,ended_at,items_json) "
                           "VALUES('KD20261001-0001','','express','closed','2026-10-01 09:00:00','2026-10-01 09:05:00','[]')")
        cls.old_sid = c.lastrowid
        old.db.execute('INSERT INTO unplanned_meta(sid,note) VALUES(?,?)', (cls.old_sid, '旧登记'))
        old.db.commit()
        old.ensure_batch(cls.old_sid, 'ZTO7788990011')
        old.db.close()
        cls.old_digest = _digest(path)
        cls.express_path = path

    @classmethod
    def tearDownClass(cls):
        # Windows 上库文件开着时删不掉临时目录：先关掉本测试打开的无采购单 / 快递单 / 联合收货库
        cls.f.http.shutdown()
        for name in ('UNPLANNED', 'EXPRESS', 'JOINT'):
            inst = getattr(getattr(cls.s, name, None), 'instance', None)
            store = getattr(inst, 'store', None)
            if store is not None:
                try:
                    store.db.close()
                except Exception:
                    pass
        cls.f.close()

    def express_new(self, tracking, note=''):
        r = self.f.call('/api/express/action', dict(action='new', note=note, tracking_no=tracking))
        self.assertTrue(r.get('ok'), r)
        return r['session']['id']

    def extra(self, q):
        import urllib.parse
        r = self.f.call('/api/receipts/extra-history?q=' + urllib.parse.quote(q))
        self.assertTrue(r.get('ok'), r)
        return r

    # ---------- 旧数据：先跑，确保 express.db 还没被新登记写过 ----------
    def test_0_existing_registrations_searchable_without_migration(self):
        r = self.extra('zto7788')
        rows = [x for x in r['rows'] if x['source'] == 'express']
        self.assertEqual([x['id'] for x in rows], [self.old_sid])
        self.assertEqual(rows[0]['trackings'][0]['tracking_no'], 'ZTO7788990011')
        self.assertIn('旧登记', rows[0]['description'])
        hits = self.nb.registrations(self.s, 'ZTO7788990011')
        self.assertEqual([(h['source'], h['sessionId'], h['tracking'], h['label']) for h in hits],
                         [('快递单', self.old_sid, 'ZTO7788990011', '快递单登记')])
        self.assertEqual(hits[0]['key'], '快递单:%d:%d' % (self.old_sid, hits[0]['batchId']))
        # 只读：搜索没有改动任何登记数据
        self.assertEqual(_digest(self.express_path), self.old_digest)

    # ---------- 记录查询 ----------
    def test_record_query_finds_express_registration(self):
        sid = self.express_new('YT9000123456', '测试快递')
        for q in ('YT9000123456', 'yt9000123456', '9000123', '  YT9000123456  '):
            rows = [x for x in self.extra(q)['rows'] if x['source'] == 'express']
            self.assertEqual([x['id'] for x in rows], [sid], q)
            self.assertEqual([t['tracking_no'] for t in rows[0]['trackings']], ['YT9000123456'], q)
            self.assertEqual(rows[0]['label'][:2], 'KD')
        # 第二个物流单号登记在同一批里，也能搜到
        with self.s.state_lock:
            self.s.EXPRESS.store.ensure_batch(sid, 'JT5550001888')
        rows = [x for x in self.extra('JT5550001888')['rows'] if x['source'] == 'express']
        self.assertEqual([x['id'] for x in rows], [sid])
        self.assertEqual([t['tracking_no'] for t in rows[0]['trackings']], ['JT5550001888'])

    def test_record_query_other_source_failure_does_not_hide_express(self):
        sid = self.express_new('SF7001002003')

        class BrokenJoint:
            instance = object()

            def history(self, *a, **k):
                raise RuntimeError('请先登录聚水潭')
        saved = self.s.JOINT
        self.s.JOINT = BrokenJoint()
        try:
            r = self.extra('SF7001002003')
        finally:
            self.s.JOINT = saved
        self.assertEqual([x['id'] for x in r['rows'] if x['source'] == 'express'], [sid])
        self.assertEqual(len(r['errors']), 1)
        self.assertIn('联合收货', r['errors'][0])

    # ---------- 录像查询（电脑端） ----------
    def test_native_registrations_cover_every_source(self):
        sid = self.express_new('YD3141592653')
        un = self.f.call('/api/unplanned/action', dict(action='new', note='', tracking_no='YD3141592999'))
        hits = self.nb.registrations(self.s, 'YD31415')
        sources = {h['source'] for h in hits}
        self.assertIn('快递单', sources)
        self.assertTrue(un.get('ok'), un)
        self.assertIn('无采购单', sources)
        ex = [h for h in hits if h['source'] == '快递单']
        self.assertEqual([h['sessionId'] for h in ex], [sid])
        po = self.nb.registrations(self.s, 'sf-test-001')
        self.assertEqual([(h['source'], h['sessionId'], h['label']) for h in po], [('普通', self.f.sid, '采购单收货')])
        self.assertEqual(po[0]['total'], 10.0)
        self.assertEqual(self.nb.registrations(self.s, ''), [])
        self.assertEqual(self.nb.registrations(self.s, 'NO-SUCH-TRACKING'), [])
        # HTTP 入口（录像窗口走这里）
        import json
        import urllib.request
        req = urllib.request.Request(self.f.base + '/api/native/registrations?q=YD3141592653',
                                     headers={'X-Native-Token': os.environ['JST_NATIVE_TOKEN']})
        with urllib.request.urlopen(req, timeout=15) as res:
            r = json.load(res)
        self.assertTrue(r.get('ok'), r)
        self.assertEqual([h['tracking'] for h in r['rows']], ['YD3141592653'])

    # ---------- 检索规则 ----------
    def test_like_escapes_wildcards_and_missing_tables(self):
        db = sqlite3.connect(':memory:')
        self.assertEqual(tracking_search.hits(db, '快递单', 'X'), [])
        db.close()
        self.assertEqual(tracking_search.like(' a%b_c\\ '), '%a\\%b\\_c\\\\%')
        self.assertEqual(self.nb.registrations(self.s, '%'), [])
        self.assertEqual(self.nb.registrations(self.s, '_'), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
