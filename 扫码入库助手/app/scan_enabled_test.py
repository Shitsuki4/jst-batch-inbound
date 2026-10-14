"""Offline enabled-candidate tests: desktop/PDA/unplanned, isolated fixture DBs only."""
import json, unittest, uuid
from pathlib import Path
from codes import product_enabled, enabled_scan_candidates
from catalog import scope_key
from pda_test import Fixture
from unplanned import UnplannedService

class EnabledFlagTests(unittest.TestCase):
    def test_explicit_flags_only(self):
        for value in (True,1,'True','true',' 1 ','启用','已启用','enabled'):
            self.assertIs(product_enabled(value),True)
        for value in (False,0,-1,'-1','False','false',' 0 ','禁用','已禁用','disabled'):
            self.assertIs(product_enabled(value),False)
        for value in (None,'','unknown','2','正常',{},[]):
            self.assertIsNone(product_enabled(value))
    def test_unknown_retained_and_off_unchanged(self):
        rows=[{'sku_id':'A','enabled':True},{'sku_id':'X','enabled':None},{'sku_id':'D','enabled':False}]
        self.assertEqual([x['sku_id'] for x in enabled_scan_candidates(rows,True)],['A','X'])
        self.assertIs(enabled_scan_candidates(rows,False),rows)
        self.assertEqual(enabled_scan_candidates([],True),[])
        with self.assertRaisesRegex(ValueError,'全部已禁用'): enabled_scan_candidates([rows[-1]],True)

class EnabledScanTests(unittest.TestCase):
    def setUp(self):
        self.f=Fixture();self.s=self.f.server;self.key=scope_key('1500','1200','1500');self.up=None
        self.seed()
    def tearDown(self):
        if self.up:self.up.store.db.close()
        self.f.close()
    def seed(self,a=True,x=False,more=None):
        rows=[dict(sku_id='A',sku_code='690000000001',aux_codes='SHARED',name='测试商品A',enabled=a),
              dict(sku_id='X',sku_code='690000000001',aux_codes='SHARED',name='测试商品X',enabled=x)]
        self.s.CATALOG.replace(self.key,'测试货主',rows+(more or []))
    def body(self,code='690000000001',qty=0,**kw):return dict(sid=self.f.sid,batch_id=self.f.batch['id'],code=code,qty=qty,**kw)
    def scan(self,code='690000000001',**kw):return self.f.call('/api/item/scan',self.body(code,**kw))
    def setting(self,value):return self.f.call('/api/settings',{'scan_ignore_disabled':value})
    def total(self):return sum(x['qty'] for x in self.s.STORE.batch_lines(self.f.batch['id']))
    def test_setting_default_and_validation(self):
        self.assertEqual(self.f.call('/api/settings')['scan_ignore_disabled'],'1')
        self.assertFalse(self.setting('maybe')['ok']);self.assertEqual(self.s.STORE.get_kv('scan_ignore_disabled','1'),'1')
        self.assertTrue(self.setting('0')['ok']);self.assertEqual(self.f.call('/api/settings')['scan_ignore_disabled'],'0')
        self.assertTrue(self.setting('1')['ok'])
    def test_pt62_pt25_filter_by_status_not_case_or_name(self):
        rows=[dict(sku_id='pt62',sku_code='6903148350997',aux_codes='PT-TEST-AUX',name='测试同款商品',enabled='-1'),
              dict(sku_id='PT25',sku_code='6903148350997',aux_codes='PT-TEST-AUX',name='名称写停用但状态启用的测试商品',enabled='1')]
        self.s.CATALOG.replace(self.key,'用户示例模拟（非实时ERP）',rows)
        for code in ('6903148350997','PT-TEST-AUX'):
            r=self.scan(code);self.assertTrue(r['ok'],r);self.assertFalse(r.get('multi'));self.assertEqual(r['item']['sku_id'],'PT25')
        rows[0]['enabled']='1';rows[1]['enabled']='-1';self.s.CATALOG.replace(self.key,'状态反转模拟',rows)
        self.assertEqual(self.scan('PT-TEST-AUX')['item']['sku_id'],'pt62')

    def test_barcode_active_purchase_without_popup(self):
        r=self.scan();self.assertTrue(r['ok'],r);self.assertFalse(r.get('multi'));self.assertEqual(r['item']['sku_id'],'A')
    def test_auxiliary_active_without_popup(self):
        r=self.scan('SHARED');self.assertTrue(r['ok'],r);self.assertFalse(r.get('multi'));self.assertEqual(r['item']['sku_id'],'A')
    def test_active_outside_does_not_remap_to_disabled_purchase(self):
        self.seed(False,True);r=self.scan();self.assertEqual(r['item']['sku_id'],'X');self.assertTrue(r['unmatched'])
    def test_multiple_active_keep_popup(self):
        self.seed(True,True);r=self.scan();self.assertTrue(r['multi']);self.assertEqual({v['sku_id'] for v in r['candidates']},{'A','X'})
    def test_unknown_with_active_keep_popup(self):
        self.seed(True,None);self.assertTrue(self.scan()['multi'])
    def test_unknown_with_disabled_not_dropped(self):
        self.seed(None,False);r=self.scan();self.assertTrue(r['ok'],r);self.assertEqual(r['item']['sku_id'],'A')
    def test_all_disabled_block_no_write(self):
        before=self.total();self.seed(False,'false');r=self.scan(qty=1)
        self.assertFalse(r['ok']);self.assertIn('全部已禁用',r['msg']);self.assertEqual(self.total(),before)
    def test_direct_disabled_sku_block(self):
        self.seed(False,True);r=self.scan('A');self.assertFalse(r['ok']);self.assertIn('禁用',r['msg'])
    def test_off_restores_all_candidates_and_old_selection(self):
        self.setting('0');r=self.scan();self.assertTrue(r['multi']);it=next(x for x in r['candidates'] if x['sku_id']=='X')
        r=self.f.call('/api/item/choose',dict(self.body(qty=1),item=it));self.assertTrue(r['ok'],r)
    def test_stale_manual_choice_cannot_bypass(self):
        self.seed(True,True);it=next(x for x in self.scan()['candidates'] if x['sku_id']=='X');self.seed(True,False);before=self.total()
        r=self.f.call('/api/item/choose',dict(self.body(qty=1),item=it));self.assertFalse(r['ok']);self.assertEqual(self.total(),before)
    def test_dry_confirmation_rechecks_current_choice(self):
        self.assertEqual(self.scan()['item']['sku_id'],'A');self.seed(False,True);before=self.total()
        r=self.scan(qty=1,expected_sku_id='A',expected_matched=True);self.assertFalse(r['ok']);self.assertEqual(self.total(),before)
    def test_current_catalog_status_overrides_old_purchase_status(self):
        session=self.s.STORE.get_session(self.f.sid);items=json.loads(session['items_json'])
        for row in items: row['enabled']=False
        self.s.STORE.db.execute('UPDATE session SET items_json=? WHERE id=?',(json.dumps(items),self.f.sid));self.s.STORE.db.commit()
        self.assertEqual(self.scan()['item']['sku_id'],'A')
    def test_other_owner_not_used(self):
        self.s.CATALOG.replace(scope_key('1500','OTHER','1500'),'其他货主',[dict(sku_id='A',enabled=False),dict(sku_id='X',enabled=True)])
        self.assertEqual(self.scan()['item']['sku_id'],'A')
    def test_status_check_still_applies_to_direct_purchase_when_lookup_off(self):
        self.s.STORE.set_kv('sku_lookup','0');self.seed(False,True);r=self.scan('A');self.assertFalse(r['ok']);self.assertIn('禁用',r['msg'])
    def test_pda_aux_uses_same_switch(self):
        self.f.pair();r=self.f.http_call('/api/item/scan',self.body('SHARED',qty=1),{'X-Request-ID':uuid.uuid4().hex})[1]
        self.assertTrue(r['ok'],r);self.assertFalse(r.get('multi'));self.assertEqual(r['item']['sku_id'],'A')
        self.seed(False,False);before=self.total();r=self.f.http_call('/api/item/scan',self.body('SHARED',qty=1),{'X-Request-ID':uuid.uuid4().hex})[1]
        self.assertFalse(r['ok']);self.assertIn('禁用',r['msg']);self.assertEqual(self.total(),before)
    def unplanned(self):
        self.up=UnplannedService(Path(self.f.temp.name)/'scan-unplanned.db',self.s.JST,lambda *a:None,self.s.lookup_item_by_code,scan_filter=self.s.scan_enabled_candidates)
        sid=self.up.new('test')['session']['id'];self.up.store.db.execute("UPDATE session SET owner_co_id='1200',auth_co_id='1500' WHERE id=?",(sid,));self.up.store.db.commit();return sid
    def test_unplanned_filter_and_live_setting(self):
        sid=self.unplanned();self.assertEqual([v['sku_id'] for v in self.up.candidates(sid,'SHARED')],['A'])
        self.setting('0');self.assertEqual({v['sku_id'] for v in self.up.candidates(sid,'SHARED')},{'A','X'})
        self.setting('1');self.seed(False,False)
        with self.assertRaisesRegex(ValueError,'禁用'):self.up.candidates(sid,'SHARED')
    def test_unplanned_existing_line_cannot_hide_disabled_status(self):
        sid=self.unplanned();bid=self.up.store.ensure_batch(sid,'TEST-ONLY')[0]['id'];self.up.store.add_qty(bid,sid,dict(sku_id='X',sku_code='690000000001',name='曾收货X'),1)
        self.assertEqual([v['sku_id'] for v in self.up.candidates(sid,'690000000001')],['A'])
        with self.assertRaisesRegex(ValueError,'禁用'):
            self.up.mutate('scan',dict(sid=sid,batch_id=bid,revision=self.up.state(sid)['meta']['revision'],code='X',sku_id='X',qty=1))

if __name__=='__main__':unittest.main(verbosity=2)
