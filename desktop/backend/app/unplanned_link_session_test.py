"""无采购单收货「匹配采购单」遇到聚水潭登录页时的处理（4.1.12）。

现场：重新登录后匹配仍报「[E-UNP-1000] 采购单查询未完成：登录态失效」。
原因：discover 会把本账号能看到的每个分仓都查一遍；聚水潭对无权访问的分仓返回登录页，
客户端据此抛 SessionExpired，discover 遇到第一个就中断整次查询并改成 E-UNP-1000。
账号本身没掉线，所以重新登录也没用。
"""
import json, os, tempfile, unittest

from inbound_test import FakeJst
from jst import SessionExpired
from unplanned import UnplannedService
from unplanned_link import discover


class ScopedJst(FakeJst):
    """本账号 coid=WAREHOUSE；DENIED 分仓无权访问，聚水潭对它返回登录页。"""

    def __init__(self):
        super().__init__()
        self.expired = False          # True：整个账号会话失效
        self.denied = {'DENIED'}
        self.searches = []

    def partners(self, timeout=10):
        return [{'id': 'OWNER'}]

    def wms_list(self, timeout=10):
        return [{'id': 'WAREHOUSE'}, {'id': 'DENIED'}]

    def search_po(self, keyword, owner, auth, id_type='po_id', page_size=25):
        self.searches.append((keyword, owner, auth, id_type))
        if self.expired or auth in self.denied:
            raise SessionExpired()
        if keyword == '123' and id_type == 'po_id' and owner in ('OWNER', '2147483647') and auth == 'WAREHOUSE':
            return [{'po_id': '123', 'owner_co_id': 'OWNER', 'wms_co_id': 'WAREHOUSE', 'status': 'Confirmed'}], {}
        return [], {}


class DiscoverLoginPageTest(unittest.TestCase):
    def test_denied_scope_login_page_does_not_abort_query(self):
        j = ScopedJst()
        found, warnings = discover(j, '123', ('OWNER', 'WAREHOUSE'))
        self.assertEqual([(c['head']['po_id'], c['owner'], c['auth']) for c in found], [('123', 'OWNER', 'WAREHOUSE')])
        self.assertTrue(any('DENIED' in w and '登录页' in w for w in warnings), warnings)
        self.assertIn(('123', '2147483647', 'DENIED', 'po_id'), j.searches)

    def test_po_only_in_later_scope_still_found_after_denied_hint(self):
        j = ScopedJst()
        # 批次记下的范围恰好是无权访问的分仓：以前第一步就中断
        found, _ = discover(j, '123', ('OWNER', 'DENIED'))
        self.assertEqual(found[0]['auth'], 'WAREHOUSE')

    def test_real_expiry_keeps_e_jst_1101(self):
        j = ScopedJst(); j.expired = True
        with self.assertRaises(SessionExpired) as cm:
            discover(j, '123', ('OWNER', 'WAREHOUSE'))
        self.assertEqual(cm.exception.error_code, 'E-JST-1101')
        self.assertIn('请重新登录', cm.exception.user_message())

    def test_not_logged_in_is_session_expired(self):
        j = ScopedJst(); j.uid = ''
        with self.assertRaises(SessionExpired):
            discover(j, '123', ('OWNER', 'WAREHOUSE'))

    def test_captcha_still_aborts_with_original_error(self):
        from jst import CallbackRejected
        j = ScopedJst()
        def boom(*a, **k): raise CallbackRejected('需要输入验证码')
        j.search_po = boom
        with self.assertRaises(CallbackRejected):
            discover(j, '123', ('OWNER', 'WAREHOUSE'))


class ErrorCodeTest(unittest.TestCase):
    def test_unplanned_api_reports_e_jst_1101_not_e_unp_1000(self):
        import errors as errreg
        self.assertEqual(errreg.classify(SessionExpired(), '登录态失效', '/api/unplanned/action'), 'E-JST-1101')


class FrontendReloginHintTest(unittest.TestCase):
    def read(self, name):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web', name), encoding='utf-8') as f:
            return f.read()

    def test_e_jst_1101_gets_relogin_steps_in_both_error_spots(self):
        import re, shutil, subprocess
        js = self.read('unplanned.js')
        self.assertIn('window.UnplannedRelogin=reloginText', js)
        self.assertIn('window.UnplannedRelogin', self.read('unplanned-link.js'))   # 匹配弹窗里的报错
        fn = re.search(r'function reloginText\(m\)\{.*?\n', js).group(0)
        node = shutil.which('node')
        if not node: self.skipTest('没有 node')
        out = subprocess.run([node, '-e', fn+'console.log(JSON.stringify([reloginText("[E-JST-1101] 聚水潭登录态失效"),reloginText("[E-UNP-1000] x")]))'],
                             capture_output=True, text=True, check=True).stdout
        hit, other = json.loads(out)
        self.assertIn('退出登录', hit); self.assertIn('不会删除', hit)
        self.assertEqual(other, '[E-UNP-1000] x')


class MatchAfterReloginTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.j = ScopedJst()
        self.j.detail_rows = [dict(sku_id='A', qty=12, in_qty=0, poi_id='11', name='商品A', price=19.5),
                              dict(sku_id='B', qty=8, in_qty=0, poi_id='12', name='商品B', price=25)]
        self.s = UnplannedService(os.path.join(self.tmp.name, 'unplanned.db'), self.j,
                                  lambda no, **scope: ({'po_id': no}, {'owner': 'OWNER', 'auth': 'WAREHOUSE'}),
                                  lambda *a, **k: [])
        self.sid = self.s.new('测试', owner='OWNER', auth='WAREHOUSE')['session']['id']; self.bid = 0
        self.bid = self.change('tracking', tracking_no='SF001')['current_batch']
        self.change('manual', sku_id='A', name='商品A', sku_code='', spec='规格', qty=5)
        self.bid = self.change('tracking', tracking_no='SF002')['current_batch']
        self.change('manual', sku_id='B', name='商品B', sku_code='', spec='规格', qty=8)

    def tearDown(self): self.s.store.db.close(); self.tmp.cleanup()

    def rev(self): return self.s.state(self.sid)['meta']['revision']

    def change(self, a, **kw):
        return self.s.mutate(a, dict(sid=self.sid, batch_id=self.bid, revision=self.rev(), **kw))

    def snapshot(self):
        st = self.s.state(self.sid)
        return st['total'], sorted(b['tracking_no'] for b in st['batches'])

    def link(self):
        r = self.change('match_search', po_id='123')
        self.assertEqual(len(r['link_candidates']), 1)
        sel = r['link_candidates'][0]['selection']
        pv = self.change('match_preview', selection=sel)['link_preview']
        return self.change('match_commit', selection=pv['selection'], fingerprint=pv['fingerprint'],
                           confirmed=True, confirm_scope=False)

    def test_expired_then_relogin_links_and_keeps_scans(self):
        before = self.snapshot()
        self.j.expired = True
        with self.assertRaises(SessionExpired) as cm:
            self.change('match_search', po_id='123')
        self.assertEqual(cm.exception.error_code, 'E-JST-1101')
        self.assertEqual(self.snapshot(), before)          # 已扫物流单和实收不丢
        self.j.expired = False                              # 用户重新登录
        st = self.link()
        self.assertTrue(st['linked'])
        self.assertEqual(self.snapshot(), before)

    def test_denied_warehouse_does_not_block_link(self):
        before = self.snapshot()
        st = self.link()
        self.assertTrue(st['linked'])
        self.assertEqual(str(st['session']['po_id']), '123')
        self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
