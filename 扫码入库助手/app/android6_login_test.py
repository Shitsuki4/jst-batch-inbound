"""Offline targeted tests. Uses loopback and synthetic cookies only."""
import base64, hashlib, http.client, json, socket, tempfile, time, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import download
from jst import JstClient, LoginError, BizError, UC_SMS_SEND, UC_SMS_LOGIN

class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='jst-download-test-')
        self.path = Path(self.tmp.name)
        self.data = b'PK' + bytes(range(256)) * 5
        (self.path/'pda.apk').write_bytes(self.data)
        (self.path/'manifest.json').write_text(json.dumps({'version':'2.0.5','sha256':hashlib.sha256(self.data).hexdigest()}))
        self.host = download.InstallerHost(SimpleNamespace(LAN=SimpleNamespace(addresses=lambda:['127.0.0.1'])), self.path)
        with socket.socket() as s:
            s.bind(('127.0.0.1',0)); self.port=s.getsockname()[1]
    def tearDown(self):
        self.host.stop(); self.tmp.cleanup()
    def start(self):
        result=self.host.start('127.0.0.1',self.port)
        self.page=result['url'].split(str(self.port),1)[1]
        return result
    def request(self,path,method='GET',headers=None):
        c=http.client.HTTPConnection('127.0.0.1',self.port,timeout=3)
        try:
            c.request(method,path,headers=headers or {});r=c.getresponse()
            return r.status,dict(r.getheaders()),r.read()
        finally: c.close()
    def test_default_off(self):
        self.assertFalse(self.host.status()['enabled']);self.assertEqual(self.host.status()['url'],'')
    def test_download_and_head(self):
        self.start(); status,headers,data=self.request(self.page+'/pda.apk')
        self.assertEqual((status,data),(200,self.data));self.assertEqual(headers['Cache-Control'],'no-store')
        status,headers,data=self.request(self.page+'/pda.apk','HEAD')
        self.assertEqual((status,data),(200,b''));self.assertEqual(int(headers['Content-Length']),len(self.data))
    def test_qr_and_page(self):
        r=self.start(); svg=base64.b64decode(r['qr'].split(',',1)[1])
        self.assertIn(b'http://www.w3.org/2000/svg',svg)
        status,h,b=self.request(self.page);self.assertEqual(status,200)
        self.assertIn(self.page.encode()+b'/pda.apk',b);self.assertIn(hashlib.sha256(self.data).hexdigest().encode(),b)
    def test_range(self):
        self.start();status,h,b=self.request(self.page+'/pda.apk',headers={'Range':'bytes=2-12'})
        self.assertEqual((status,b),(206,self.data[2:13]));self.assertEqual(h['Content-Range'],'bytes 2-12/1282')
        for value in ('bytes=9999-','bytes=9-2','garbage','bytes=1-2,4-5'):
            self.assertEqual(self.request(self.page+'/pda.apk',headers={'Range':value})[0],416)
    def test_only_signed_package_route(self):
        self.start()
        for p in ('/','/api/state','/api/item/scan','/../manifest.json',self.page+'/../pda.apk','/d/wrong/pda.apk'):
            self.assertEqual(self.request(p)[0],404,p)
        self.assertEqual(self.request(self.page+'/pda.apk','POST')[0],405)
        self.assertEqual(self.request(self.page,headers={'Host':'evil.example'})[0],403)
    def test_expiry_and_restart(self):
        first=self.start()['url'];old=self.host.server;self.host.expires=time.time()-1
        self.assertEqual(self.request(self.page)[0],410);self.assertFalse(self.host.status()['enabled'])
        self.host.stop();self.assertNotEqual(self.start()['url'],first)
        self.host.stop(expected=old);self.assertTrue(self.host.status()['enabled'])
    def test_metadata_tamper(self):
        (self.path/'pda.apk').write_bytes(b'changed')
        with self.assertRaises(ValueError):self.start()
        self.assertFalse(self.host.status()['enabled'])
    def test_bad_metadata(self):
        for value in ('[]','null','{','{}'):
            (self.path/'manifest.json').write_text(value)
            self.assertTrue(self.host.status()['error'])
    def test_invalid_bind(self):
        for host,port in [('0.0.0.0',self.port),('8.8.8.8',self.port),('127.0.0.1',80)]:
            with self.assertRaises(ValueError):self.host.start(host,port)
    def test_qr_failure_no_socket_leak(self):
        with patch.object(self.host,'_make_qr',side_effect=RuntimeError('encoder missing')):
            with self.assertRaises(RuntimeError):self.start()
        self.assertIsNone(self.host.server)
        with socket.socket() as s:s.bind(('127.0.0.1',self.port))
    def test_double_start(self):
        self.start()
        with self.assertRaises(ValueError):self.start()

class SmsTests(unittest.TestCase):
    def setUp(self): self.j=JstClient()
    def test_send_preserves_challenge_cookie(self):
        with patch.object(self.j,'_json_login_post',return_value=({'code':0,'data':{'cId':'opaque','isLoginId':False}}, {'set_cookie':['challenge=abc; HttpOnly','gone=deleted']})) as call:
            self.assertEqual(self.j.sms_send(' user ','12')['cid'],'opaque')
            self.assertEqual(call.call_args.args,(UC_SMS_SEND,{'data':{'mobile':'user','suffix':'12'}}))
            self.assertEqual(self.j.cookies,{'challenge':'abc'})
    def test_login_request_and_response(self):
        self.j.cookies={'challenge':'abc'}
        with patch.object(self.j,'_json_login_post',return_value=({'code':0,'data':{'isBinding':True}}, {'set_cookie':['u_id=123; HttpOnly','u_co_id=456','u_name=Test%20User']})) as call:
            info=self.j.sms_login(' user ',' 123456 ','opaque','12')
            self.assertEqual(info['uid'],'123');self.assertEqual(info['uname'],'Test User')
            self.assertEqual(call.call_args.args[0],UC_SMS_LOGIN)
            self.assertEqual(call.call_args.args[1]['data']['cid'],'opaque')
            self.assertEqual(call.call_args.args[1]['data']['suffix'],'12')
            self.assertEqual(self.j.cookies['challenge'],'abc')
    def test_missing_identity_does_not_reuse_previous_user(self):
        self.j.cookies={'u_id':'old','u_co_id':'oldco'}
        with patch.object(self.j,'_json_login_post',return_value=({'code':0,'data':{'isBinding':True}},{})):
            with self.assertRaises(LoginError):self.j.sms_login('u','123456','cid')
    def test_risk_and_unbound_and_bad_code(self):
        for result in ({'code':1,'msg':'invalid'},{'code':0,'data':{'isBinding':False}},{'code':0,'data':{'isBinding':True,'hasRisk':True}}):
            with patch.object(self.j,'_json_login_post',return_value=(result,{})):
                with self.assertRaises(LoginError):self.j.sms_login('u','123456','cid')
    def test_returned_cookie_identity(self):
        with patch.object(self.j,'_json_login_post',return_value=({'code':0,'data':{'isBinding':True},'cookie':{'u_id':'123','u_co_id':'456'}},{})):
            self.assertEqual(self.j.sms_login('u','123456','cid')['coid'],'456')
    def test_mobile_login_omits_empty_suffix(self):
        result = ({'code':0,'data':{'isBinding':True}},
                  {'set_cookie':['u_id=123','u_co_id=456']})
        with patch.object(self.j, '_json_login_post', return_value=result) as call:
            self.j.sms_login('user', '123456', 'opaque')
            self.assertNotIn('suffix', call.call_args.args[1]['data'])
            self.assertEqual(call.call_args.args[1]['data']['cid'], 'opaque')

    def test_unspecified_rejection_does_not_blame_code(self):
        with patch.object(self.j, '_json_login_post', return_value=({'code':301109}, {})):
            with self.assertRaises(LoginError) as error:
                self.j.sms_login('user', '123456', 'opaque')
            self.assertIn('301109', str(error.exception))
            self.assertNotIn('验证码错误', str(error.exception))
            self.assertNotIn('123456', str(error.exception))
            self.assertNotIn('opaque', str(error.exception))

    def test_official_rejection_preserves_message_and_status(self):
        with patch.object(self.j, '_json_login_post', return_value=({'code':123,'msg':'验证未通过'}, {})):
            with self.assertRaises(LoginError) as error:
                self.j.sms_login('user', '123456', 'opaque')
            self.assertIn('验证未通过', str(error.exception))
            self.assertIn('服务端错误码：123', str(error.exception))

    def test_apply_session_rejects_partial(self):
        for ck in ({},{'u_id':'1'},{'u_co_id':'2'}):
            with self.assertRaises(LoginError):self.j.adopt_cookies(ck)

if __name__=='__main__':unittest.main(verbosity=2)
