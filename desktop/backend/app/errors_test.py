# -*- coding: utf-8 -*-
"""错误码与「不再一律报无权限」的回归测试。

1. 覆盖：用户看到的报错都带已登记的错误码。
   - 本机服务每条失败响应（_fail）都以 [E-类别-四位数字] 开头，并带 error_code / 日志编号；
   - 代码里出现的每个错误码（Python / JS / C# / 安装脚本）都已登记；
   - 桌面程序不再用不带码的 MessageBox 报错，安装程序的报错弹窗都带 E-INS 码；
   - 每个接口 / 异常类型都能归到一个已登记的码。
2. 断网、超时、聚水潭业务报错、采购单不对、服务器出错、本机服务出错都不会被说成权限问题；
   只有聚水潭明确返回权限拒绝才是 E-JST-1401。
"""
import os
import re
import socket
import sqlite3
import unittest
import urllib.error

import errors
import jst
from inbound_http_test import Fixture

HERE = os.path.dirname(os.path.abspath(__file__))
DESKTOP = os.path.abspath(os.path.join(HERE, '..', '..'))
REPO = os.path.abspath(os.path.join(DESKTOP, '..'))
CODE_RX = re.compile(r'\bE-[A-Z]{2,5}-\d{4}\b')
HEAD_RX = re.compile(r'^\[E-[A-Z]{2,5}-\d{4}\] ')
PERM_TEXT = '无此操作权限'


def files(root, exts, skip=('node_modules', 'bin', 'obj', '.git', 'dist', 'build', '__pycache__')):
    for d, dirs, names in os.walk(root):
        dirs[:] = [x for x in dirs if x not in skip]
        for n in names:
            if n.endswith(exts):
                yield os.path.join(d, n)


def read(p):
    with open(p, 'rb') as f:
        return f.read().decode('utf-8-sig', errors='replace')


# 代表各类「不是权限问题」的失败：(说明, 异常, 期望的错误码)
def non_permission_failures():
    return [
        ('断网', jst.network_error(urllib.error.URLError(ConnectionRefusedError(10061, 'refused')), 'https://x/a?token=1'), 'E-NET-2001'),
        ('DNS 失败', jst.network_error(urllib.error.URLError(socket.gaierror(11001, 'getaddrinfo failed'))), 'E-NET-2001'),
        ('超时', jst.network_error(socket.timeout('timed out')), 'E-NET-2002'),
        ('读取超时', jst.network_error(urllib.error.URLError(socket.timeout('timed out'))), 'E-NET-2002'),
        ('服务器出错', jst.JstHttpError(502, 'https://x'), 'E-JST-1001'),
        ('HTTP 4xx', jst.JstHttpError(404, 'https://x'), 'E-JST-1002'),
        ('登录失效', jst.SessionExpired(), 'E-JST-1101'),
        ('返回无法解析', jst.JstParseError('bad json'), 'E-JST-1201'),
        ('聚水潭业务校验', jst.rejected('采购单状态为已完成，不允许入库'), 'E-JST-1301'),
        ('聚水潭空原因拒绝', jst.rejected(''), 'E-JST-1301'),
        ('采购单不存在', jst.BizError('PO_NOT_FOUND', code='E-PO-1001', user_text='未找到采购单 X'), 'E-PO-1001'),
        ('单号为空', jst.BizError('单号为空', code='E-PO-2008'), 'E-PO-2008'),
        ('本地数据库出错', sqlite3.OperationalError('database is locked'), 'E-DATA-1001'),
        ('本地文件出错', PermissionError(13, 'Access is denied'), 'E-DATA-1002'),
        ('程序出错', KeyError('po_id'), 'E-APP-9001'),
    ]


class RegistryTests(unittest.TestCase):
    def test_codes_are_well_formed_and_documented(self):
        self.assertGreater(len(errors.REGISTRY), 50)
        for code, (meaning, fix) in errors.REGISTRY.items():
            self.assertRegex(code, r'^E-[A-Z]{2,5}-\d{4}$')
            self.assertTrue(meaning.strip() and fix.strip(), code)
        self.assertEqual(errors.PERMISSION, 'E-JST-1401')

    def test_every_code_used_in_source_is_registered(self):
        unknown = {}
        roots = [HERE, os.path.join(DESKTOP, 'src'), os.path.join(DESKTOP, 'installer')]
        for root in roots:
            for p in files(root, ('.py', '.js', '.html', '.cs', '.iss', '.ps1')):
                for code in CODE_RX.findall(read(p)):
                    if code not in errors.REGISTRY and not code.endswith('-0000'):
                        unknown.setdefault(code, os.path.relpath(p, DESKTOP))
        self.assertEqual(unknown, {}, '代码里用了未登记的错误码')

    def test_docs_list_every_code(self):
        doc = os.path.join(REPO, 'docs', '错误码.md')
        self.assertTrue(os.path.exists(doc), '缺少 docs/错误码.md')
        text = read(doc)
        missing = [c for c in errors.REGISTRY if c not in text]
        self.assertEqual(missing, [], 'docs/错误码.md 没有列出这些码，请用 python errors.py --markdown 重新生成')

    def test_native_shell_has_no_uncoded_error_dialogs(self):
        bad = []
        for p in files(os.path.join(DESKTOP, 'src'), ('.cs',)):
            if p.endswith('NativeDiag.cs'):
                continue
            for n, line in enumerate(read(p).splitlines(), 1):
                for m in re.finditer(r'MessageBox\.Show\(', line):
                    call = line[m.start():m.start() + 600]
                    # 询问类（是 / 否、确定 / 取消）不是报错，允许
                    if 'MessageBoxButton.YesNo' in call or 'MessageBoxButton.OKCancel' in call:
                        continue
                    bad.append('%s:%d' % (os.path.basename(p), n))
                if re.search(r'throw new Exception\(', line) and os.path.basename(p) == 'AppUpdate.cs':
                    bad.append('%s:%d throw without code' % (os.path.basename(p), n))
        self.assertEqual(bad, [], '桌面程序报错请用 NativeDiag.Show / CodedException，带上错误码')

    def test_native_log_matches_backend_rules(self):
        cs = read(os.path.join(DESKTOP, 'src', 'NativeDiag.cs'))
        import diaglog
        self.assertIn('MaxBytes=2*1024*1024', cs.replace(' ', ''))
        self.assertIn('Backups=5', cs.replace(' ', ''))
        self.assertIn('"native-diagnostic.jsonl"', cs)
        self.assertEqual(diaglog.MAX_BYTES, 2 * 1024 * 1024)
        self.assertEqual(diaglog.BACKUPS, 5)
        for word in ('password', 'token', 'cookie', 'sign'):
            self.assertIn(word, cs.lower())

    def test_installer_error_dialogs_have_codes(self):
        bad = []
        for name in ('offline_setup.iss', 'deps_setup.iss'):
            text = read(os.path.join(DESKTOP, 'installer', name))
            lines = text.splitlines()
            for n, line in enumerate(lines, 1):
                m = re.search(r"(?:MsgBox|RaiseException)\(\s*'([^']*)", line)
                call = ' '.join(lines[n - 1:n + 3])
                if not m or m.group(1).startswith('已取消') or 'mbConfirmation' in call or 'mbInformation' in call:
                    continue
                if not m.group(1).startswith('[E-INS-'):
                    bad.append('%s:%d' % (name, n))
            self.assertIn('[E-INS-', text, name)
        self.assertEqual(bad, [], '安装程序报错请加 [E-INS-xxxx]')

    def test_classify_always_returns_registered_code(self):
        samples = [None, ValueError('x'), RuntimeError('boom'), OSError('disk'), sqlite3.IntegrityError('UNIQUE'),
                   urllib.error.URLError('x'), socket.timeout()]
        paths = ['/api/po/create', '/api/inbound/submit', '/api/unknown/thing', '', '/api/diag/export', '/api/po/rematch/confirm']
        for exc in samples:
            for path in paths:
                self.assertIn(errors.classify(exc, '', path), errors.REGISTRY, (exc, path))
        self.assertEqual(errors.format_msg('E-PO-1001', '未找到'), '[E-PO-1001] 未找到')
        self.assertEqual(errors.format_msg('E-PO-1001', '[E-PO-1001] 未找到'), '[E-PO-1001] 未找到')


class NotPermissionTests(unittest.TestCase):
    def test_failures_keep_their_own_code(self):
        for label, exc, code in non_permission_failures():
            got = errors.classify(exc, getattr(exc, 'user_message', lambda: str(exc))(), '/api/po/create')
            self.assertEqual(got, code, label)
            self.assertNotEqual(got, errors.PERMISSION, label)
            self.assertNotIsInstance(exc, jst.PermissionDenied, label)

    def test_only_explicit_permission_denial_is_permission(self):
        for text in ('当前账号没有权限', '您无权限操作此功能', '权限不足'):
            e = jst.rejected(text)
            self.assertIsInstance(e, jst.PermissionDenied)
            self.assertEqual(errors.classify(e), 'E-JST-1401')
        for text in ('采购单不存在', '数量超过可入库数', '系统繁忙', '登录已过期'):
            self.assertNotEqual(errors.classify(jst.rejected(text)), 'E-JST-1401', text)


class ServiceResponseTests(unittest.TestCase):
    """本机服务的真实响应：查采购单时各种失败都如实报码，不说成「无权限」。"""

    @classmethod
    def setUpClass(cls):
        cls.f = Fixture()

    @classmethod
    def tearDownClass(cls):
        cls.f.close()

    def assertCoded(self, r, label):
        self.assertFalse(r.get('ok'), (label, r))
        self.assertRegex(r.get('msg') or '', HEAD_RX, label)
        self.assertIn(r.get('error_code'), errors.REGISTRY, label)
        self.assertTrue(r.get('request_id') or r.get('rid') or r.get('log_id'), (label, r))
        self.assertEqual(r['msg'].split(']')[0][1:], r['error_code'], label)

    def with_lookup_failure(self, exc):
        srv = self.f.server
        orig_search, orig_ctx = getattr(srv.JST, 'search_po', None), srv._ctx_candidates

        def boom(*a, **k):
            raise exc
        srv.JST.search_po = boom
        srv._ctx_candidates = lambda *a, **k: [('OWNER', 'WAREHOUSE')]
        try:
            return (self.f.call('/api/po/create', {'no': 'PO-NET-1'}),
                    self.f.call('/api/po/rematch/preview', {'sid': self.f.sid, 'no': 'PO-NET-1'}))
        finally:
            srv.JST.search_po, srv._ctx_candidates = orig_search, orig_ctx

    def test_po_lookup_failures_are_not_permission_or_not_found(self):
        for label, exc, code in non_permission_failures():
            if not isinstance(exc, jst.BizError) or code in ('E-PO-1001', 'E-PO-2008'):
                continue
            for r in self.with_lookup_failure(exc):
                self.assertCoded(r, label)
                self.assertEqual(r['error_code'], code, (label, r))
                self.assertNotIn(PERM_TEXT, r['msg'], label)
                self.assertNotIn('未找到采购单', r['msg'], label)

    def test_explicit_denial_still_reports_permission(self):
        for r in self.with_lookup_failure(jst.rejected('该账号没有权限查看采购单')):
            self.assertCoded(r, 'permission')
            self.assertEqual(r['error_code'], 'E-JST-1401')

    def test_local_failures_are_coded(self):
        cases = [('/api/po/rematch/preview', {'sid': 999999, 'no': 'X'}),
                 ('/api/po/rematch/confirm', {'sid': self.f.sid, 'no': 'X'}),
                 ('/api/session/resume', {'sid': 999999}),
                 ('/api/inbound/preview', {'sid': 999999})]
        for path, body in cases:
            r = self.f.call(path, body)
            self.assertCoded(r, path)
            self.assertNotEqual(r['error_code'], 'E-JST-1401', path)


if __name__ == '__main__':
    unittest.main()
