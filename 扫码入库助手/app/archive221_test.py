import unittest
from purchase_watch_test import PurchaseWatchTest
class Archive221Test(PurchaseWatchTest):
 def test_manual_archive_reopen_poll_persistent(self):
  self.w.transition(self.wid,'matched');r=self.w.archive(self.wid,'人工确认',True)
  self.assertTrue(r['complete']);self.assertEqual(self.w.listing()['red'],0)
  self.w.poll();self.assertTrue(self.get()['complete'])
  r=self.w.transition(self.wid,'matched');self.assertFalse(r['complete']);self.w.poll();self.assertFalse(self.get()['complete']);self.assertEqual(self.w.listing()['red'],1)
  cap=self.w.capture(self.get());result=self.w.read(self.get(),cap,self.j);result.update(inbound_complete=True,po_complete=True)
  r=self.w.apply(self.get(),cap,result);self.assertFalse(r['complete'])
 def test_archive_source_change_warns(self):
  self.w.transition(self.wid,'matched');self.w.archive(self.wid,'',True)
  cap=self.w.capture(self.get());cap['receipt']='changed';result=self.w.read(self.get(),cap,self.j)
  r=self.w.apply(self.get(),cap,result);self.assertTrue(r['complete']);self.assertTrue(r['completion_changed'])
 def test_archive_confirmation_required(self):
  with self.assertRaises(ValueError):self.w.archive(self.wid,'',True)
  self.w.transition(self.wid,'matched')
  with self.assertRaises(ValueError):self.w.archive(self.wid,'',False)
if __name__=='__main__':unittest.main()
