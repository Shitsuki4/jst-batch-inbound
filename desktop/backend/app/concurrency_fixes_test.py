# -*- coding: utf-8 -*-
"""Offline regression for the backend concurrency / PDA / native-port / scan-idempotency fixes.

Never touches real ERP or production data (FakeJst / StockFake, temp directories).
Run: python concurrency_fixes_test.py
"""
import json
import os
import socket
import sqlite3
import ssl
import tempfile
import threading
import time
import unittest
import uuid

from pda_test import Fixture
from jst import JstClient
from store import Store, validate_qty


def op():
    return uuid.uuid4().hex


class Base(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.s = self.f.server
        self.sid, self.bid = self.f.sid, self.f.batch['id']

    def tearDown(self):
        self.f.close()

    def qty(self, sku='A'):
        return next(l['qty'] for l in self.s.STORE.batch_lines(self.bid) if l['sku_id'] == sku)

    def scan(self, **kw):
        b = dict(sid=self.sid, batch_id=self.bid, code='A', qty=1)
        b.update(kw)
        return self.f.call('/api/item/scan', b)


# ---------------------------------------------------------------- A. SQLite / locking
class RollbackTests(Base):
    def test_store_multi_statement_write_is_atomic(self):
        st = self.s.STORE
        st.db.execute("CREATE TEMP TRIGGER block_batch_delete BEFORE DELETE ON batch BEGIN SELECT RAISE(ABORT,'blocked'); END")
        before = len(st.batch_lines(self.bid))
        with self.assertRaises(sqlite3.DatabaseError):
            st.del_batch(self.bid)            # DELETE line succeeds, DELETE batch aborts
        self.assertFalse(st.db.in_transaction)
        self.assertEqual(len(st.batch_lines(self.bid)), before)   # no partial delete survived
        st.db.execute("DROP TRIGGER block_batch_delete")

    def test_failed_post_leaves_no_partial_rows(self):
        H = self.s.Handler
        def crash(h):
            h._body()
            self.s.STORE.db.execute("INSERT INTO kv(k,v) VALUES('half_written','1')")
            raise RuntimeError('boom after first statement')
        def caught(h):
            h._body()
            self.s.STORE.db.execute("INSERT INTO kv(k,v) VALUES('half_written2','1')")
            return h._fail('handled')
        H._post_api_test_crash, H._post_api_test_caught = crash, caught
        try:
            self.assertFalse(self.f.call('/api/test/crash', {})['ok'])
            self.assertFalse(self.f.call('/api/test/caught', {})['ok'])
            with self.s.state_lock:   # the handler's rollback runs (under the lock) right after its response
                self.assertFalse(self.s.STORE.db.in_transaction)
            # A later successful write must not publish the abandoned rows.
            self.assertTrue(self.f.call('/api/settings', {'sound': '1'})['ok'])
            self.assertIsNone(self.s.STORE.get_kv('half_written'))
            self.assertIsNone(self.s.STORE.get_kv('half_written2'))
        finally:
            del H._post_api_test_crash, H._post_api_test_caught


class LockedGetTests(Base):
    def _get_async(self, path):
        out = {}
        t = threading.Thread(target=lambda: out.setdefault('r', self.f.call(path)), daemon=True)
        t.start()
        return t, out

    def test_api_gets_wait_for_state_lock_but_heartbeat_does_not(self):
        lock = self.s.state_lock
        lock.acquire()
        try:
            started = time.monotonic()
            self.assertTrue(self.f.call('/api/heartbeat')['ok'])        # lock-free
            self.assertLess(time.monotonic() - started, 2)
            threads = [self._get_async(p) for p in ('/api/state', '/api/diff?sid=%d' % self.sid,
                                                    '/api/partners', '/api/recents', '/api/settings')]
            time.sleep(0.6)
            for t, out in threads:
                self.assertTrue(t.is_alive(), 'GET must not run while a POST holds state_lock')
        finally:
            lock.release()
        for t, out in threads:
            t.join(10)
            self.assertFalse(t.is_alive())
            self.assertIn('r', out)

    def test_concurrent_scans_and_reads_are_consistent(self):
        start = self.qty()
        errors = []
        def writer():
            for _ in range(15):
                r = self.scan(client_op_id=op())
                if not r.get('ok'): errors.append(r)
        def reader():
            for _ in range(15):
                r = self.f.call('/api/state?sid=%d' % self.sid)
                if 'session' not in r: errors.append(r)
                d = self.f.call('/api/diff?sid=%d' % self.sid)
                if not d.get('ok'): errors.append(d)
        ts = [threading.Thread(target=writer) for _ in range(3)] + [threading.Thread(target=reader) for _ in range(3)]
        for t in ts: t.start()
        for t in ts: t.join(60)
        self.assertEqual(errors, [])
        self.assertEqual(self.qty(), start + 45)        # every distinct scan still adds
        with self.s.state_lock:
            self.assertFalse(self.s.STORE.db.in_transaction)


class CookieTests(unittest.TestCase):
    def test_header_iteration_while_absorbing(self):
        c = JstClient()
        c.cookies = {'k%d' % i: 'v' for i in range(50)}
        stop, errors = threading.Event(), []
        def read():
            while not stop.is_set():
                try: c._cookie_header()
                except RuntimeError as e: errors.append(e)
        def write():
            for i in range(20000):
                c._absorb_set_cookie('n%d=v; Path=/' % (i % 300))
                c._absorb_set_cookie('n%d=deleted' % ((i + 7) % 300))
        r = threading.Thread(target=read); r.start()
        w = threading.Thread(target=write); w.start(); w.join(); stop.set(); r.join()
        self.assertEqual(errors, [])
        self.assertIn('k1=v', c._cookie_header())


class PartnersGenerationTests(Base):
    def test_inflight_partner_fetch_does_not_restore_cache_after_login(self):
        s = self.s
        s.STORE.set_many({'partners_json': '', 'partners_ts': '0'})
        entered, release = threading.Event(), threading.Event()
        old = s.JST.partners
        def slow():
            entered.set(); release.wait(5)
            return [{'id': 'OLD', 'name': '旧账号货主'}]
        s.JST.partners = slow
        try:
            out = {}
            t = threading.Thread(target=lambda: out.setdefault('r', s.load_partners(refresh=True)))
            t.start(); self.assertTrue(entered.wait(5))
            s.invalidate_partners()          # login / account switch while the fetch is in flight
            release.set(); t.join(5)
            self.assertEqual(s.STORE.get_kv('partners_json'), '')
            self.assertTrue(out['r'].get('err'))
        finally:
            s.JST.partners = old


# ---------------------------------------------------------------- B. PDA
class PdaSlowBodyTests(Base):
    def _raw(self, path, length, body=b'', origin=True):
        host = self.f.host
        sock = socket.create_connection((host.host, host.port), timeout=10)
        tls = self.f.ctx.wrap_socket(sock, server_hostname=host.host)
        hdr = ('POST %s HTTP/1.1\r\nHost: %s:%d\r\nContent-Type: application/json\r\nX-JST-Pair: 1\r\n'
               'Content-Length: %d\r\n%s\r\n') % (path, host.host, host.port, length,
                                                    ('Origin: %s\r\n' % self.f.url) if origin else '')
        tls.sendall(hdr.encode() + body)
        return tls

    def test_slow_unauthenticated_body_never_holds_state_lock(self):
        tls = self._raw('/api/pda/pair', 100, b'{"co', origin=False)   # trickle, never finish
        try:
            time.sleep(0.5)
            got = self.s.state_lock.acquire(timeout=1)
            self.assertTrue(got, 'slow PDA sender must not hold the business lock')
            self.s.state_lock.release()
            # Desktop request is served while the PDA body is still incomplete.
            self.assertTrue(self.f.call('/api/settings', {'sound': '1'})['ok'])
            started = time.monotonic()
            tls.settimeout(15)
            chunks = []
            while True:
                try: c = tls.recv(4096)
                except (ssl.SSLError, OSError): break
                if not c: break
                chunks.append(c)
            resp = b''.join(chunks).decode('utf-8', 'replace')
            self.assertLess(time.monotonic() - started, 9)      # total body deadline ~5s
            self.assertIn('"ok": false', resp)
        finally:
            tls.close()

    def test_pair_body_capped_at_1kb(self):
        r = self.f.http_call('/api/pda/pair', {'code': '1', 'name': 'x' * 2000}, {'Origin': '', 'X-JST-Pair': '1'})
        self.assertFalse(r[1]['ok']); self.assertIn('过大', r[1]['msg'])

    def test_authenticated_slow_body_does_not_hold_lock(self):
        self.f.pair()
        host = self.f.host
        sock = socket.create_connection((host.host, host.port), timeout=10)
        tls = self.f.ctx.wrap_socket(sock, server_hostname=host.host)
        hdr = ('POST /api/item/scan HTTP/1.1\r\nHost: %s:%d\r\nContent-Type: application/json\r\nOrigin: %s\r\n'
               'Cookie: %s\r\nContent-Length: 200\r\n\r\n{') % (host.host, host.port, self.f.url, self.f.cookie)
        tls.sendall(hdr.encode())
        try:
            time.sleep(0.5)
            self.assertTrue(self.s.state_lock.acquire(timeout=1)); self.s.state_lock.release()
        finally:
            tls.close()


class PdaPairingLimitTests(Base):
    def test_rate_limit_is_per_source_ip(self):
        h = self.f.host
        h.pairing()
        for _ in range(10):
            with self.assertRaises(ValueError): h.pair('', 'x', '10.0.0.9')   # invalid, not "wrong code"
        with self.assertRaisesRegex(ValueError, '过多'): h.pair('', 'x', '10.0.0.9')
        p = h.pairing()
        token = h.pair(p['code'], 'other-device', '10.0.0.10')   # another device is not blocked
        self.assertTrue(token)

    def test_wrong_codes_burn_current_pairing_code(self):
        h = self.f.host
        p = h.pairing()
        wrong = '1' if p['code'] != '1' else '2'
        for i in range(4):
            with self.assertRaisesRegex(ValueError, '无效或过期'): h.pair(wrong, 'x', '10.0.1.%d' % i)
        with self.assertRaisesRegex(ValueError, '重新生成'): h.pair(wrong, 'x', '10.0.1.99')
        with self.assertRaises(ValueError): h.pair(p['code'], 'x', '10.0.2.1')   # code is gone
        p = h.pairing()
        self.assertTrue(h.pair(p['code'], 'x', '10.0.2.1'))                       # regenerate works


# ---------------------------------------------------------------- C. native port
class NativePortTests(Base):
    def test_strict_native_bind_exits_75(self):
        blocker = socket.socket(); blocker.bind(('127.0.0.1', 0)); blocker.listen(1)
        port = blocker.getsockname()[1]
        try:
            with self.assertRaises(SystemExit) as cm:
                self.s.create_http_server(port, native=True)
            self.assertEqual(cm.exception.code, 75)
            srv, used = self.s.create_http_server(port, native=False)    # standalone keeps fallback
            try: self.assertNotEqual(used, port)
            finally: srv.server_close()
        finally:
            blocker.close()

    def test_strict_native_bind_uses_given_port(self):
        probe = socket.socket(); probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]; probe.close()
        srv, used = self.s.create_http_server(port, native=True)
        try: self.assertEqual((used, srv.server_address[1]), (port, port))
        finally: srv.server_close()

    @unittest.skipIf(os.name == 'nt', 'Windows mutex is exercised on Windows only')
    def test_mutex_noop_off_windows(self):
        self.assertIsNone(self.s.hold_native_mutex())


# ---------------------------------------------------------------- 1. idempotent scans
class ClientOpTests(Base):
    def test_same_op_id_counts_once_new_ids_keep_accumulating(self):
        start, oid = self.qty(), op()
        r1 = self.scan(qty=2, client_op_id=oid); self.assertTrue(r1['ok'], r1)
        r2 = self.scan(qty=2, client_op_id=oid); self.assertTrue(r2['ok'], r2)
        self.assertTrue(r2['duplicate']); self.assertEqual(r2['line_id'], r1['line_id']); self.assertEqual(r2['qty'], r1['qty'])
        self.assertEqual(self.qty(), start + 2)
        for _ in range(3): self.assertTrue(self.scan(client_op_id=op())['ok'])
        self.assertTrue(self.scan()['ok'])                     # old clients without an id still add
        self.assertEqual(self.qty(), start + 2 + 4)

    def test_reused_id_with_different_content_refused(self):
        start, oid = self.qty(), op()
        self.assertTrue(self.scan(qty=1, client_op_id=oid)['ok'])
        r = self.scan(qty=5, client_op_id=oid)
        self.assertFalse(r['ok']); self.assertEqual(self.qty(), start + 1)
        self.assertFalse(self.scan(client_op_id='bad id!')['ok'])

    def test_parallel_retries_of_one_op(self):
        import concurrent.futures
        start, oid = self.qty(), op()
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            rs = list(pool.map(lambda _: self.scan(qty=3, client_op_id=oid), range(4)))
        self.assertTrue(all(r['ok'] for r in rs), rs)
        self.assertEqual(self.qty(), start + 3)

    def test_dry_resolve_does_not_consume_op_id(self):
        start, oid = self.qty(), op()
        self.assertTrue(self.scan(qty=0, client_op_id=oid)['dry'])
        self.assertTrue(self.scan(qty=1, client_op_id=oid)['ok'])
        self.assertEqual(self.qty(), start + 1)

    def test_choose_and_line_set_idempotent(self):
        it = {'sku_id': 'B', 'poi_id': '2'}
        b = dict(sid=self.sid, batch_id=self.bid, code='B', qty=2, item=it, client_op_id=op())
        start = self.qty('B')
        r = self.f.call('/api/item/choose', b); self.assertTrue(r['ok'], r)
        r = self.f.call('/api/item/choose', b); self.assertTrue(r['ok'] and r['duplicate'], r)
        self.assertEqual(self.qty('B'), start + 2)
        lid = next(l['id'] for l in self.s.STORE.batch_lines(self.bid) if l['sku_id'] == 'B')
        sb = dict(line_id=lid, qty=9, sid=self.sid, batch_id=self.bid, client_op_id=op())
        self.assertTrue(self.f.call('/api/line/set', sb)['ok'])
        self.s.STORE.set_qty(lid, 4)                      # someone corrects it afterwards
        self.assertTrue(self.f.call('/api/line/set', sb)['duplicate'])
        self.assertEqual(self.qty('B'), 4)                # the retry did not overwrite it


# ---------------------------------------------------------------- 4. integer quantities
class IntegerQtyTests(Base):
    def test_validator(self):
        for good, n in ((1, 1), (3.0, 3), ('7', 7), (' 8 ', 8), ('2.0', 2), (10 ** 9, 10 ** 9)):
            self.assertEqual(validate_qty(good), n)
        self.assertEqual(validate_qty(0, allow_zero=True), 0)
        for bad in (True, False, None, 1.5, '1.5', -1, '-2', float('inf'), float('nan'), 'abc', '', 10 ** 9 + 1, 2e9, [], {}):
            with self.assertRaises(ValueError, msg=repr(bad)): validate_qty(bad)
        with self.assertRaises(ValueError): validate_qty(0)

    def test_scan_choose_set_reject_bad_quantities(self):
        start = self.qty()
        for q in (1.5, '2.5', -1, True, 2e9, 'abc'):
            r = self.scan(qty=q); self.assertFalse(r['ok'], (q, r))
        req = json.dumps(dict(sid=self.sid, batch_id=self.bid, code='A', qty=float('inf')))
        import urllib.request
        with urllib.request.urlopen(urllib.request.Request(self.f.base + '/api/item/scan', data=req.encode(),
                                    headers={'Content-Type': 'application/json'}), timeout=10) as res:
            self.assertFalse(json.load(res)['ok'])
        self.assertEqual(self.qty(), start)
        b = dict(sid=self.sid, batch_id=self.bid, code='B', item={'sku_id': 'B', 'poi_id': '2'})
        for q in (0, 0.5, -3):
            self.assertFalse(self.f.call('/api/item/choose', dict(b, qty=q))['ok'])
        lid = next(l['id'] for l in self.s.STORE.batch_lines(self.bid) if l['sku_id'] == 'A')
        self.assertFalse(self.f.call('/api/line/set', dict(line_id=lid, qty=1.5))['ok'])
        self.assertFalse(self.f.call('/api/line/set', dict(line_id=lid, qty=-1))['ok'])
        self.assertTrue(self.f.call('/api/line/set', dict(line_id=lid, qty=0))['ok'])
        self.assertEqual(self.qty(), 0)

    def test_scan_step_must_be_positive_integer(self):
        for bad in ('0.5', '-1', '0', 'x', '1.5'):
            self.assertFalse(self.f.call('/api/settings', {'scan_step': bad})['ok'], bad)
        self.assertTrue(self.f.call('/api/settings', {'scan_step': '3'})['ok'])
        start = self.qty()
        b = dict(sid=self.sid, batch_id=self.bid, code='A')     # no qty -> settings step
        self.assertTrue(self.f.call('/api/item/scan', b)['ok'])
        self.assertEqual(self.qty(), start + 3)
        self.s.STORE.set_kv('scan_step', '0.5')                # legacy bad value falls back to 1
        self.assertTrue(self.f.call('/api/item/scan', b)['ok'])
        self.assertEqual(self.qty(), start + 4)

    def test_pda_rejects_fraction(self):
        self.f.pair()
        r = self.f.http_call('/api/item/scan', dict(sid=self.sid, batch_id=self.bid, code='A', qty=1.5),
                             {'X-Request-ID': op()})[1]
        self.assertFalse(r['ok'])


# ---------------------------------------------------------------- 5a. same SKU on several PO lines
class DuplicateSkuLineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = Store(os.path.join(self.tmp.name, 'jst.db'))
        items = [{'poi_id': '1', 'sku_id': 'A', 'name': 'A', 'qty': 5, 'qty_known': True, 'in_qty': 0, 'in_qty_known': True},
                 {'poi_id': '2', 'sku_id': 'C', 'name': 'C', 'qty': 1, 'qty_known': True, 'in_qty': 0, 'in_qty_known': True},
                 {'poi_id': '3', 'sku_id': 'A', 'name': 'A', 'qty': 3, 'qty_known': True, 'in_qty': 0, 'in_qty_known': True}]
        self.sid = self.st.open_session('PO', 'PO', 'po_id', {}, items)
        self.bid = self.st.ensure_batch(self.sid, 'T1')[0]['id']

    def tearDown(self):
        self.st.db.close(); self.tmp.cleanup()

    def add(self, sku, n):
        self.st.add_qty(self.bid, self.sid, {'sku_id': sku, 'name': sku}, n)

    def test_reports_aggregate_planned_per_sku(self):
        self.add('A', 6); self.add('C', 1)
        diff = self.st.diff_report(self.sid)
        self.assertEqual([(d['sku_id'], d['plan'], d['recv']) for d in diff], [('A', 8.0, 6.0)])
        summary = {r['sku_id']: r for r in self.st.po_received_summary(self.sid)}
        self.assertEqual(len(self.st.po_received_summary(self.sid)), 2)
        self.assertEqual((summary['A']['plan'], summary['A']['recv'], summary['A']['done']), (8.0, 6.0, False))
        bal = {r['sku_id']: r for r in self.st.purchase_balance(self.sid)}
        self.assertEqual(bal['A']['ordered'], summary['A']['plan'])
        self.assertEqual(bal['A']['local_pending'], summary['A']['recv'])
        self.add('A', 2)
        self.assertEqual(self.st.diff_report(self.sid), [])
        self.add('A', 1)
        self.assertEqual([(d['plan'], d['recv'], d['reason']) for d in self.st.diff_report(self.sid)], [(8.0, 9.0, '多收 1')])

    def test_po_items_split_received_across_lines(self):
        import server
        old = server.STORE
        server.STORE = self.st
        try:
            self.add('A', 6)
            rows = server.po_items(self.sid)
            a = [r for r in rows if r['sku_id'] == 'A']
            self.assertEqual([(r['plan'], r['recv'], r['done']) for r in a], [(5.0, 5.0, True), (3.0, 1.0, False)])
            self.assertEqual(sum(r['recv'] for r in a), 6.0)
            self.assertTrue(all(r['sku_plan'] == 8.0 and r['sku_recv'] == 6.0 for r in a))
            self.add('A', 3)
            a = [r for r in server.po_items(self.sid) if r['sku_id'] == 'A']
            self.assertEqual([(r['recv'], r['over']) for r in a], [(5.0, False), (4.0, True)])   # overflow on last line
        finally:
            server.STORE = old


if __name__ == '__main__':
    unittest.main(verbosity=2)
