"""Offline deletion, account boundaries, durable tombstones and polling race checks."""
import copy, json, unittest
from unittest.mock import patch
from purchase_watch_test import PurchaseWatchTest
from purchase_watch import PurchaseWatch

class PurchaseWatchDeleteTest(unittest.TestCase):
    setUp = PurchaseWatchTest.setUp
    tearDown = PurchaseWatchTest.tearDown
    change = PurchaseWatchTest.change
    get = PurchaseWatchTest.get
    make_modified = PurchaseWatchTest.make_modified
    service = PurchaseWatchTest.service
    create = PurchaseWatchTest.create

    def test_confirmation_required(self):
        for value in (None, False, 'true', 1):
            with self.assertRaises(ValueError): self.w.delete(self.wid,value)
        self.assertEqual(len(self.w.listing()['rows']),1)

    def test_delete_only_tracking_and_notice(self):
        before=self.u.state(self.sid)
        self.w.transition(self.wid,'matched');self.w.transition(self.wid,'pending')
        self.assertEqual(self.w.listing()['red'],1)
        self.w.delete(self.wid,True)
        self.assertEqual(self.w.listing()['rows'],[])
        self.assertEqual((self.w.listing()['red'],self.w.listing()['yellow']),(0,0))
        self.assertEqual(before,self.u.state(self.sid))
        self.assertTrue(self.w.get(self.wid,include_deleted=True)['deleted'])
        self.assertIn('删除待改单',self.w.db.execute('SELECT action FROM audit ORDER BY id DESC LIMIT 1').fetchone()[0])

    def test_other_states_and_completed_reject(self):
        for state in ('modified','matched'):
            self.w.transition(self.wid,state)
            with self.assertRaises(ValueError): self.w.delete(self.wid,True)
        self.w.transition(self.wid,'pending');w=self.get();w['complete']=True;self.w.save(w)
        with self.assertRaises(ValueError):self.w.delete(self.wid,True)

    def test_account_rejects(self):
        self.j.coid='OTHER'
        with self.assertRaises(ValueError):self.w.delete(self.wid,True)
        self.assertEqual(self.w.listing()['rows'],[])

    def test_poll_never_resurrects(self):
        self.w.delete(self.wid,True)
        with patch.object(self.w,'read',side_effect=AssertionError('Deleted record polled')):self.w.poll()
        self.assertEqual(self.w.listing()['rows'],[])
        for op in (lambda:self.w.get(self.wid),lambda:self.w.transition(self.wid,'matched'),lambda:self.service(),lambda:self.w.delete(self.wid,True)):
            with self.assertRaises(ValueError):op()

    def test_restart_retains_tombstone(self):
        path=self.w.db.execute('PRAGMA database_list').fetchone()[2];self.w.delete(self.wid,True);self.w.db.close()
        self.w=PurchaseWatch(path,self.j,lambda:self.stores,self.lock)
        self.assertEqual(self.w.listing()['rows'],[])

    def test_reregister_reuses_identity_and_new_generation(self):
        token=self.get()['tracking_token'];self.w.delete(self.wid,True)
        restored=self.w.register('unplanned',self.sid)
        self.assertEqual(restored['id'],self.wid);self.assertEqual(restored['state'],'pending')
        self.assertNotEqual(restored['tracking_token'],token);self.assertEqual(restored['notice'],'')
        self.w.poll();self.assertEqual(len(self.w.listing()['rows']),1)
        self.assertEqual(self.w.register('unplanned',self.sid)['id'],self.wid)

    def test_late_poll_result_dropped_after_reregister(self):
        original=self.w.read
        def race(w,cap,client):
            result=original(w,cap,client)
            self.w.delete(self.wid,True);self.w.register('unplanned',self.sid)
            return result
        with patch.object(self.w,'read',side_effect=race):self.w.poll()
        self.assertEqual(self.get()['checked_at'],'');self.assertEqual(self.get()['rows'],[])

    def test_late_poll_failure_dropped_after_reregister(self):
        def race(*args):
            self.w.delete(self.wid,True);self.w.register('unplanned',self.sid)
            raise TimeoutError('stale error')
        with patch.object(self.w,'read',side_effect=race):self.w.poll()
        self.assertEqual(self.get()['error'],'')

    def test_inflight_delete_stays_deleted(self):
        original=self.w.read
        def race(w,cap,client):
            result=original(w,cap,client);self.w.delete(self.wid,True);return result
        with patch.object(self.w,'read',side_effect=race):self.w.poll()
        self.assertEqual(self.w.listing()['rows'],[])

    def test_inbound_jobs_survive_delete_reregister(self):
        self.make_modified();job=self.create();self.w.transition(self.wid,'pending')
        saved=copy.deepcopy(self.u.store.get_inbound(job['id']))
        self.w.delete(self.wid,True);self.w.register('unplanned',self.sid);self.w.poll()
        self.assertEqual(saved,self.u.store.get_inbound(job['id']))
        self.assertTrue(any(s['jobs'] for s in self.get()['sources']))

if __name__=='__main__':unittest.main(verbosity=2)
