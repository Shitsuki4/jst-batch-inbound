# -*- coding: utf-8 -*-
"""Restore HTTP wiring: confirm gate, pre-restore backup, write block while staged, cancel."""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

_TMP = tempfile.TemporaryDirectory(prefix='jst-restore-http-')
os.environ['JST_DB_DIR'] = _TMP.name
os.environ['JST_NO_BROWSER'] = '1'
os.environ.pop('JST_NATIVE_PORT', None)
import server  # noqa: E402
import restore  # noqa: E402


class RestoreHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = server.DesktopHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=cls.http.serve_forever, daemon=True).start()
        cls.base = 'http://127.0.0.1:%d' % cls.http.server_address[1]
        cls.backup = server.BACKUPS.create('manual')
        cls.bid = os.path.basename(restore.list_backups(server.BACKUPS.root)[0]['directory'])

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown(); cls.http.server_close()

    def call(self, path, body=None):
        req = urllib.request.Request(self.base + path, headers={'Content-Type': 'application/json'},
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=15) as r: return json.load(r)
        except urllib.error.HTTPError as e:
            with e: return json.load(e)

    def tearDown(self):
        self.call('/api/restore/cancel', {})

    def test_list_and_preview(self):
        r = self.call('/api/restore/list')
        self.assertTrue(r['ok'], r); self.assertFalse(r['pending'])
        self.assertIn(self.bid, [os.path.basename(b['directory']) for b in r['backups']])
        p = self.call('/api/restore/preview?id=' + self.bid)
        self.assertTrue(p['ok'], p); self.assertTrue(p['verify']['ok'], p)
        self.assertFalse(self.call('/api/restore/preview?id=../x')['ok'])

    def test_stage_refused_without_confirm(self):
        for body in ({'id': self.bid}, {'id': self.bid, 'confirm': 'yes'}):
            r = self.call('/api/restore/stage', body)
            self.assertFalse(r['ok'], r)
        r = self.call('/api/restore/stage', {'id': self.bid, 'confirm': '恢复', 'recording': True})
        self.assertFalse(r['ok'], r)
        self.assertFalse(os.path.isdir(os.path.join(_TMP.name, restore.PENDING)))

    def test_stage_refused_with_unfinished_inbound(self):
        db = server.STORE.db
        db.execute("PRAGMA foreign_keys=OFF")
        cur = db.execute("INSERT INTO inbound_job(session_id,fingerprint,snapshot_json,login_co_id,status,created_at,updated_at) "
                         "VALUES(999,'fp-restore-http','{}','1','unknown','t','t')")
        db.commit()
        try:
            r = self.call('/api/restore/stage', {'id': self.bid, 'confirm': '恢复'})
        finally:
            db.execute("DELETE FROM inbound_job WHERE id=?", (cur.lastrowid,)); db.commit()
        self.assertFalse(r['ok'], r); self.assertIn('入库', r['msg'])
        self.assertFalse(os.path.isdir(os.path.join(_TMP.name, restore.PENDING)))

    def test_write_blocked_while_staged_and_cancel_unblocks(self):
        before = len(restore.list_backups(server.BACKUPS.root))
        r = self.call('/api/restore/stage', {'id': self.bid, 'confirm': '恢复'})
        self.assertTrue(r['ok'], r); self.assertTrue(r['pending'])
        rows = restore.list_backups(server.BACKUPS.root)
        self.assertEqual(len(rows), before + 1)
        self.assertIn('pre-restore', [b['reason'] for b in rows])
        self.assertEqual(r['pre_restore_label'], '恢复前自动备份')
        blocked = self.call('/api/backup/create', {})
        self.assertFalse(blocked['ok'], blocked); self.assertIn('恢复', blocked['msg'])
        self.assertTrue(self.call('/api/restore/list')['pending'])
        c = self.call('/api/restore/cancel', {})
        self.assertTrue(c['ok'], c)
        self.assertFalse(os.path.isdir(os.path.join(_TMP.name, restore.PENDING)))
        self.assertTrue(self.call('/api/backup/create', {})['ok'])

    def test_result_is_one_time(self):
        restore._write_json_atomic(os.path.join(_TMP.name, restore.RESULT), {'ok': True, 'status': 'applied'})
        self.assertEqual(self.call('/api/restore/result')['result']['status'], 'applied')
        self.assertIsNone(self.call('/api/restore/result')['result'])


if __name__ == '__main__':
    unittest.main()
