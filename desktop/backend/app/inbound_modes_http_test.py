"""HTTP mode round-trip, stale requests, concurrent choices; real HTTP with FakeJst."""
import concurrent.futures, unittest
from inbound_http_test import Fixture
class ModeHTTPTests(unittest.TestCase):
 def setUp(self):
  self.f=Fixture();s=self.f.server
  self.x=s.STORE.add_qty(self.f.batch['id'],self.f.sid,{'sku_id':'X','matched':False},5)[0]
 def tearDown(self):self.f.close()
 def preview(self,mode='purchase_only'):
  return self.f.call('/api/inbound/preview?sid=%s&wh_id=1&mode=%s'%(self.f.sid,mode))
 def body(self,mode='purchase_only'):
  p=self.preview(mode);self.assertTrue(p['ok'],p)
  return dict(sid=self.f.sid,wh_id='1',mode=mode,fingerprint=p['fingerprint'],confirmed=True)
 def test_two_stages_and_duplicate(self):
  one=self.body();a=self.f.call('/api/inbound/create',one);self.assertTrue(a['ok'],a)
  two=self.body('all_received');b=self.f.call('/api/inbound/create',two);self.assertTrue(b['ok'],b)
  for body,job in [(one,a['job']),(two,b['job'])]:self.assertEqual(self.f.call('/api/inbound/create',body)['job']['id'],job['id'])
  self.assertEqual(self.f.server.JST.creates,2)
  self.assertEqual(self.preview('all_received')['total_qty'],'0')
 def test_conflicting_modes_simultaneous_one_initial_create(self):
  bodies=[self.body(),self.body('all_received')]
  with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
   results=list(pool.map(lambda body:self.f.call('/api/inbound/create',body),bodies))
  self.assertEqual(sum(bool(r['ok']) for r in results),1,results);self.assertEqual(self.f.server.JST.creates,1)
 def test_same_mode_concurrent_is_idempotent(self):
  b=self.body()
  with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:r=list(pool.map(lambda _:self.f.call('/api/inbound/create',b),range(3)))
  self.assertTrue(all(x['ok'] for x in r),r);self.assertEqual(self.f.server.JST.creates,1)
 def test_invalid_mode_and_cross_origin_no_writes(self):
  self.assertFalse(self.preview('bad')['ok']);b=self.body()
  self.assertFalse(self.f.call('/api/inbound/create',dict(b,mode='bad'))['ok'])
  self.assertFalse(self.f.call('/api/inbound/create',b,'https://unrelated.example')['ok']);self.assertEqual(self.f.server.JST.creates,0)
 def test_confirmation_mode_and_warehouse_tamper(self):
  b=self.body()
  for changes in [dict(confirmed=False),dict(confirmed='true'),dict(wh_id='3'),dict(mode='all_received')]:
   self.assertFalse(self.f.call('/api/inbound/create',dict(b,**changes))['ok'],changes)
  self.assertEqual(self.f.server.JST.creates,0)
 def test_global_setting_no_longer_controls_new_modes(self):
  b=self.body();self.f.server.STORE.set_kv('inbound_validate_purchase','0')
  self.assertTrue(self.f.call('/api/inbound/create',b)['ok']);self.assertEqual(self.f.server.JST.creates,1)
 def test_failure_resumes_snapshot_not_new_mode(self):
  b=self.body();self.f.server.JST.fail_qty=True
  self.assertFalse(self.f.call('/api/inbound/create',b)['ok'])
  self.assertFalse(self.f.call('/api/inbound/create',self.body('all_received'))['ok'])
  j=self.preview('all_received')['jobs'][0]
  r=self.f.call('/api/inbound/resume',dict(job_id=j['id'],confirmed=True));self.assertTrue(r['ok'],r)
  rows=self.f.server.JST.docs[j['io_id']]['rows'];self.assertNotIn('X',[r['sku_id'] for r in rows if float(r['qty'])])
  self.assertEqual(self.f.server.JST.creates,1)
if __name__=='__main__':unittest.main()
