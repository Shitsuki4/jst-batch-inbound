# -*- coding: utf-8 -*-
"""全局锁重构回归（logic review #8）：远程调用 / 慢 I/O 不得占用 state_lock。

全程 FakeJst + 临时库，不接触真实聚水潭。慢远程用 sleep 模拟（默认 3 秒）。
运行：python app/lock_refactor_test.py      （JST_LOCK_STRESS=N 时把并发用例重复 N 次）
"""
import concurrent.futures
import os
import threading
import time
import unittest
import uuid

from inbound_http_test import Fixture
from inbound_test import FakeJst

SLOW = float(os.environ.get("JST_FAKE_SLOW", "3"))
FAST = 0.5          # 无关请求必须在这个时间内返回
STRESS = max(1, int(os.environ.get("JST_LOCK_STRESS", "1")))


class SlowJst(FakeJst):
    """指定的方法在 armed 时先 sleep；entered 事件表示慢调用已开始（此时调用方应已释放锁）。"""

    def __init__(self):
        super().__init__()
        self.slow = set()
        self.entered = threading.Event()
        self.lookups = []
        self.once = False

    def _maybe_slow(self, name):
        if name in self.slow and not (self.once and self.entered.is_set()):
            self.entered.set()
            time.sleep(SLOW)

    def fetch_detail(self, po_id, owner, auth):
        self._maybe_slow("fetch_detail")
        return super().fetch_detail(po_id, owner, auth)

    def item_search(self, *a, **kw):
        self._maybe_slow("item_search")
        return super().item_search(*a, **kw)

    def lookup_item(self, code, owner="", auth=""):
        self.lookups.append(code)
        self._maybe_slow("lookup_item")
        return {"sku_id": "Z-" + code, "name": "实时查询商品 " + code, "sku_code": code}

    def partners(self, timeout=10):
        self._maybe_slow("partners")
        return [{"id": "OWNER", "name": "模拟货主"}]

    def wms_list(self, timeout=10):
        return [{"id": "WAREHOUSE", "name": "模拟仓库"}]


class Base(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        s = self.s = self.f.server
        from inbound import InboundService
        s.JST = SlowJst()
        s.JST.uname, s.JST.coname = "离线测试", "模拟仓库"
        s.INBOUND = InboundService(s.STORE, s.JST)
        # 第二张无关收货单，用于验证「别的会话扫码」不被阻塞
        items = [{"poi_id": "9", "sku_id": "C", "sku_code": "690000000009", "name": "测试商品 C", "qty": 50}]
        self.sid2 = s.STORE.open_session("OUT-456", "456", "po_id",
                                         {"owner_co_id": "OWNER", "auth_co_id": "WAREHOUSE"}, items)
        self.bid2 = s.STORE.ensure_batch(self.sid2, "SF-TEST-002")[0]["id"]
        self.pinned = s.STATE_LOCK.stats["pinned"]
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=8)

    def tearDown(self):
        self.pool.shutdown(wait=True)
        self.assertEqual(self.s.STATE_LOCK.stats["pinned"], self.pinned, "远程调用不应在未提交事务时发生（锁未能释放）")
        self.assertFalse(self.s.STATE_LOCK.held())
        self.f.close()

    # helpers
    def timed(self, path, body=None):
        t = time.monotonic()
        r = self.f.call(path, body)
        return r, time.monotonic() - t

    def scan2(self, n=1, op=None):
        b = dict(sid=self.sid2, batch_id=self.bid2, code="690000000009", qty=n)
        if op:
            b["client_op_id"] = op
        return self.timed("/api/item/scan", b)

    def wait_entered(self):
        self.assertTrue(self.s.JST.entered.wait(10), "慢远程调用未开始")
        time.sleep(0.05)

    def line_qty(self, sid, bid, sku):
        return sum(l["qty"] for l in self.s.STORE.batch_lines(bid) if l["sku_id"] == sku and l["session_id"] == sid)

    def assert_fast(self, elapsed, what):
        self.assertLess(elapsed, FAST, "%s 用时 %.2fs，被慢远程调用阻塞" % (what, elapsed))


class SlowRemoteDoesNotBlock(Base):
    def preview_body(self):
        p = self.f.call("/api/inbound/preview?sid=%s&wh_id=1" % self.f.sid)
        self.assertTrue(p["ok"], p)
        return dict(sid=self.f.sid, wh_id="1", fingerprint=p["fingerprint"], confirmed=True)

    def test_slow_inbound_create_keeps_state_and_other_session_responsive(self):
        body = self.preview_body()
        # 入库流程会多次查明细 / 查商品；只让第一次慢调用 sleep，避免总时长超过客户端 15 秒超时（Windows CI 较慢）。
        self.s.JST.slow = {"fetch_detail", "item_search"}
        self.s.JST.once = True
        fut = self.pool.submit(self.timed, "/api/inbound/create", body)
        self.wait_entered()
        for path in ("/api/state", "/api/state?sid=%s" % self.sid2, "/api/heartbeat", "/api/recents"):
            r, dt = self.timed(path)
            self.assert_fast(dt, "GET " + path)
        r, dt = self.scan2()
        self.assertTrue(r["ok"], r)
        self.assert_fast(dt, "另一会话扫码")
        result, total = fut.result(30)
        self.assertTrue(result["ok"], result)
        self.assertGreaterEqual(total, SLOW)
        self.assertEqual(self.s.JST.creates, 1)
        self.assertEqual(self.line_qty(self.sid2, self.bid2, "C"), 1)

    def test_scan_during_remote_check_is_detected_as_conflict(self):
        """生成入库的远程核对期间同一批次被扫码：回锁后版本不一致，拒绝用旧快照开单。"""
        body = self.preview_body()
        self.s.JST.slow = {"fetch_detail"}
        fut = self.pool.submit(self.timed, "/api/inbound/create", body)
        self.wait_entered()
        r, dt = self.timed("/api/item/scan", dict(sid=self.f.sid, batch_id=self.f.batch["id"], code="690000000001", qty=2))
        self.assertTrue(r["ok"], r)
        self.assert_fast(dt, "同批次扫码")
        result, _ = fut.result(30)
        self.assertFalse(result["ok"], result)
        self.assertIn("变化", result["msg"])
        self.assertEqual(self.s.JST.creates, 0)
        self.assertEqual(self.line_qty(self.f.sid, self.f.batch["id"], "A"), 9)   # 7 + 2，累加保留
        self.s.JST.slow = set()
        again = self.f.call("/api/inbound/create", self.preview_body())   # 重新预览后可以正常生成
        self.assertTrue(again["ok"], again)
        self.assertEqual(self.s.JST.creates, 1)

    def test_concurrent_creates_same_session_serialized_one_remote_create(self):
        body = self.preview_body()
        self.s.JST.slow = {"item_search"}
        futs = [self.pool.submit(self.timed, "/api/inbound/create", body) for _ in range(3)]
        self.wait_entered()
        r, dt = self.timed("/api/state")
        self.assert_fast(dt, "GET /api/state")
        results = [f.result(60)[0] for f in futs]
        self.assertTrue(all(x["ok"] for x in results), results)
        self.assertEqual(len({x["job"]["id"] for x in results}), 1)
        self.assertEqual(self.s.JST.creates, 1)

    def test_slow_item_lookup_does_not_block_other_session_scan(self):
        self.s.STORE.set_kv("catalog_online", "1")
        self.s.JST.slow = {"lookup_item"}
        fut = self.pool.submit(self.timed, "/api/item/scan",
                               dict(sid=self.f.sid, batch_id=self.f.batch["id"], code="UNKNOWN-1", qty=1))
        self.wait_entered()
        r, dt = self.scan2()
        self.assertTrue(r["ok"], r)
        self.assert_fast(dt, "另一会话扫码")
        r, dt = self.timed("/api/state")
        self.assert_fast(dt, "GET /api/state")
        result, total = fut.result(30)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result.get("unmatched"), result)
        self.assertGreaterEqual(total, SLOW)

    def test_slow_partners_refresh_does_not_block(self):
        self.s.JST.slow = {"partners"}
        fut = self.pool.submit(self.timed, "/api/partners?refresh=1")
        self.wait_entered()
        r, dt = self.timed("/api/state")
        self.assert_fast(dt, "GET /api/state")
        r, dt = self.scan2()
        self.assert_fast(dt, "扫码")
        result, _ = fut.result(30)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["owners"][0]["id"], "OWNER")

    def test_export_file_written_outside_lock(self):
        import xlsx
        orig = xlsx.write_xlsx
        started = threading.Event()

        def slow_write(*a, **kw):
            started.set()
            time.sleep(SLOW)
            return orig(*a, **kw)
        xlsx.write_xlsx = slow_write
        try:
            fut = self.pool.submit(self.timed, "/api/export?mode=po&value=123&format=xlsx")
            self.assertTrue(started.wait(10))
            r, dt = self.scan2()
            self.assert_fast(dt, "导出文件期间扫码")
            result, _ = fut.result(30)
            self.assertTrue(result["ok"], result)
        finally:
            xlsx.write_xlsx = orig

    def test_backup_copies_outside_lock(self):
        import backup
        lock = self.s.STATE_LOCK
        seen = []
        orig = backup.hashlib

        class Probe:
            @staticmethod
            def sha256(*a):
                # 复制阶段：其他线程应能立即拿到业务锁
                ok = []
                t = threading.Thread(target=lambda: ok.append(lock.acquire(timeout=FAST) and (lock.release() or True)))
                t.start(); t.join()
                seen.append(bool(ok and ok[0]))
                return orig.sha256(*a)
        backup.hashlib = Probe
        try:
            m = backup.BackupManager(self.f.temp.name, lock)
            with lock:          # 与手动备份请求一样在持锁的处理函数里调用
                result = m.create("manual")
            self.assertTrue(result["complete"])
            self.assertTrue(seen and all(seen), seen)
            seen.clear()
            with lock:
                m.create("pre-restore")     # 恢复前备份是写入屏障：保持持锁
            self.assertTrue(seen and not any(seen), seen)
        finally:
            backup.hashlib = orig


class ConcurrentConsistency(Base):
    def test_concurrent_same_line_scans_with_slow_lookups(self):
        """同一行并发累加 + 期间不断有锁外慢查询：数量严格等于扫码次数之和，重试不重复累加。"""
        self.s.STORE.set_kv("catalog_online", "1")
        global SLOW
        for round_ in range(STRESS):
            before = self.line_qty(self.sid2, self.bid2, "C")
            self.s.JST.slow = {"lookup_item"}
            old_slow, SLOW = SLOW, 0.05
            try:
                ops = [uuid.uuid4().hex for _ in range(40)]
                jobs = []
                for i, op in enumerate(ops):
                    jobs.append(self.pool.submit(self.scan2, 1, op))
                    if i % 4 == 0:     # 未知条码 → 锁外实时查询（让出锁）
                        jobs.append(self.pool.submit(self.timed, "/api/item/scan",
                                                     dict(sid=self.f.sid, batch_id=self.f.batch["id"],
                                                          code="UNK-%d-%d" % (round_, i), qty=1)))
                # 一半操作编号重放一次：只能入账一次
                jobs += [self.pool.submit(self.scan2, 1, op) for op in ops[::2]]
                results = [j.result(60)[0] for j in jobs]
            finally:
                SLOW = old_slow
            self.assertTrue(all(r["ok"] for r in results), [r for r in results if not r["ok"]][:3])
            self.assertEqual(self.line_qty(self.sid2, self.bid2, "C"), before + 40)
            self.assertEqual(sum(1 for r in results if r.get("duplicate")), 20)

    def test_duplicate_op_during_slow_lookup_counted_once(self):
        """同一 client_op_id 的两次请求都在锁外查询资料：回锁后重查编号，只入账一次。"""
        self.s.STORE.set_kv("catalog_online", "1")
        self.s.JST.slow = {"lookup_item"}
        global SLOW
        old_slow, SLOW = SLOW, 0.5
        try:
            body = dict(sid=self.f.sid, batch_id=self.f.batch["id"], code="UNK-DUP", qty=3, client_op_id=uuid.uuid4().hex)
            a, b = [self.pool.submit(self.timed, "/api/item/scan", body) for _ in range(2)]
            ra, rb = a.result(30)[0], b.result(30)[0]
        finally:
            SLOW = old_slow
        self.assertTrue(ra["ok"] and rb["ok"], (ra, rb))
        self.assertEqual(sum(bool(r.get("duplicate")) for r in (ra, rb)), 1, (ra, rb))
        rows = [l for l in self.s.STORE.batch_lines(self.f.batch["id"]) if "UNK-DUP" in (l["sku_id"] or "")]
        self.assertEqual([(l["sku_id"], l["qty"]) for l in rows], [("Z-UNK-DUP", 3)])

    def test_batch_deleted_during_slow_lookup_is_refused(self):
        self.s.STORE.set_kv("catalog_online", "1")
        self.s.JST.slow = {"lookup_item"}
        bid = self.s.STORE.ensure_batch(self.sid2, "SF-TEMP-DEL")[0]["id"]
        fut = self.pool.submit(self.timed, "/api/item/scan", dict(sid=self.sid2, batch_id=bid, code="UNK-DEL", qty=1))
        self.wait_entered()
        r = self.f.call("/api/batch/del", dict(batch_id=bid, sid=self.sid2))
        self.assertTrue(r["ok"], r)
        result, _ = fut.result(30)
        self.assertFalse(result["ok"], result)
        self.assertEqual(self.s.STORE.db.execute("SELECT COUNT(*) FROM line WHERE batch_id=?", (bid,)).fetchone()[0], 0)

    def test_oplog_attribution_excludes_other_requests_during_yield(self):
        from oplog import OpLog
        path = os.path.join(self.f.temp.name, "operation_log.db")
        old = self.s.OPLOG
        self.s.OPLOG = OpLog(lambda: path, lambda: "180")
        try:
            self.s.STORE.set_kv("catalog_online", "1")
            self.s.JST.slow = {"lookup_item"}
            fut = self.pool.submit(self.timed, "/api/item/scan",
                                   dict(sid=self.f.sid, batch_id=self.f.batch["id"], code="UNK-LOG", qty=1))
            self.wait_entered()
            for _ in range(3):
                r, _ = self.scan2()
                self.assertTrue(r["ok"], r)
            self.assertTrue(fut.result(30)[0]["ok"])
            rows = self.s.OPLOG.query({}, 100, 0)["rows"]
            scans = [r for r in rows if r.get("op") == "item_scan"]
            self.assertEqual(sorted(str(r.get("sku")) for r in scans), ["C", "C", "C", "Z-UNK-LOG"], scans)
        finally:
            self.s.OPLOG = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
