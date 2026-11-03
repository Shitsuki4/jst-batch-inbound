# -*- coding: utf-8 -*-
"""4.1.8：导出目录（可自定义，目录结构 根目录\\日期\\类别\\文件）和 CSV 单元格安全写法。

离线运行：python export_dir_test.py
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import exportfiles  # noqa: E402
from exportfiles import ExportDir, ExportDirError, csv_cell, category_of, day_folder  # noqa: E402


class ExportDirTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='jst-export-test-')
        self.data = os.path.join(self.tmp.name, 'data')
        self.default = os.path.join(self.tmp.name, 'backend', '导出')
        self.ex = ExportDir(self.data, self.default)

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_root_and_layout(self):
        self.assertEqual(self.ex.root(), os.path.abspath(self.default))
        at = datetime(2026, 11, 3, 17, 15)
        p = self.ex.target('收货明细_采购单123.xlsx', '收货明细', at=at)
        self.assertEqual(p, os.path.join(self.default, '2026年11月3日', '收货明细', '收货明细_采购单123.xlsx'))
        self.assertTrue(os.path.isdir(os.path.dirname(p)))
        self.assertEqual(day_folder(at), '2026年11月3日')

    def test_category_by_name(self):
        self.assertEqual(category_of('joint-receipt-20261103.xlsx'), '联合收货')
        self.assertEqual(category_of('操作记录_20261103.csv'), '操作记录')
        self.assertEqual(category_of('diagnostic-20261103.zip'), '诊断包')
        self.assertEqual(category_of('unplanned-receipt.csv'), '无采购单收货')
        self.assertEqual(category_of('whatever.bin'), exportfiles.OTHER)

    def test_same_name_never_overwrites(self):
        at = datetime(2026, 11, 3)
        p1 = self.ex.target('a.csv', '收货明细', at=at)
        open(p1, 'w').close()
        p2 = self.ex.target('a.csv', '收货明细', at=at)
        self.assertNotEqual(p1, p2)
        self.assertTrue(p2.endswith('a (2).csv'))

    def test_unsafe_names_are_cleaned(self):
        p = self.ex.target('..\\..\\evil:name?.csv', 'x/y', at=datetime(2026, 1, 1))
        root = os.path.realpath(self.ex.root())
        self.assertEqual(os.path.commonpath([root, os.path.realpath(p)]), root)
        self.assertNotIn(':', os.path.basename(p))

    def test_custom_root_persisted_and_old_files_stay(self):
        old_file = self.ex.target('a.csv', '收货明细')
        open(old_file, 'w').close()
        custom = os.path.join(self.tmp.name, '收货导出')
        r = self.ex.set_root(custom)
        self.assertEqual(r['old'], os.path.abspath(self.default))
        self.assertTrue(r['custom'])
        self.assertEqual(ExportDir(self.data, self.default).root(), os.path.abspath(custom), '重启后要读回自定义目录')
        self.assertTrue(os.path.exists(old_file), '改目录不移动旧文件')
        raw = open(self.ex.settings_path, 'rb').read()
        self.assertTrue(all(b < 128 for b in raw), '卸载程序按 ANSI 读，中文必须转义')
        self.assertEqual(json.loads(raw)['ExportDirectory'], os.path.abspath(custom))
        self.assertTrue(self.ex.target('b.csv').startswith(os.path.abspath(custom)))
        # 恢复默认
        r = self.ex.set_root('')
        self.assertFalse(r['custom'])
        self.assertEqual(self.ex.root(), os.path.abspath(self.default))

    def test_invalid_paths_have_codes(self):
        with self.assertRaises(ExportDirError) as c:
            self.ex.set_root('relative\\dir')
        self.assertEqual(c.exception.error_code, 'E-DATA-1007')
        f = os.path.join(self.tmp.name, 'file.txt')
        open(f, 'w').close()
        with self.assertRaises(ExportDirError) as c:
            self.ex.set_root(f)
        self.assertEqual(c.exception.error_code, 'E-DATA-1007')
        with self.assertRaises(ExportDirError) as c:
            self.ex.set_root(os.path.join(f, 'sub'))
        self.assertEqual(c.exception.error_code, 'E-DATA-1008')
        self.assertEqual(self.ex.root(), os.path.abspath(self.default), '设置失败不能改掉原目录')

    def test_broken_settings_fall_back_to_default(self):
        os.makedirs(self.data, exist_ok=True)
        with open(self.ex.settings_path, 'w') as f:
            f.write('{not json')
        self.assertEqual(self.ex.root(), os.path.abspath(self.default))
        with open(self.ex.settings_path, 'w') as f:
            json.dump({'ExportDirectory': 'relative'}, f)
        self.assertEqual(self.ex.root(), os.path.abspath(self.default))

    def test_resolve_stays_inside_root(self):
        p = self.ex.target('a.csv', '收货明细')
        open(p, 'w').close()
        rel = os.path.relpath(p, self.ex.root()).replace(os.sep, '/')
        self.assertEqual(self.ex.resolve(rel), os.path.realpath(p))
        self.assertIsNone(self.ex.resolve('../data/x'))
        self.assertIsNone(self.ex.resolve(''))
        self.assertIsNone(self.ex.resolve(rel + '.missing'))
        self.assertTrue(self.ex.relative_url(p).startswith('/files/'))

    def test_status_reports_unwritable(self):
        st = self.ex.status()
        self.assertTrue(st['writable'])
        self.assertIn('类别', st['layout'])


class CsvCellTests(unittest.TestCase):
    def test_long_numbers_stay_text(self):
        self.assertEqual(csv_cell('773312345678901'), '="773312345678901"')
        self.assertEqual(csv_cell('0123'), '="0123"')
        self.assertEqual(csv_cell('12345'), '12345')
        self.assertEqual(csv_cell(12345678901234), 12345678901234, '数值不动')

    def test_formula_injection(self):
        for v in ('=1+1', '+SUM(A1)', '-2', '@cmd', '＝HYPERLINK()', ' =x'):
            self.assertTrue(csv_cell(v).startswith("'"), v)
        self.assertEqual(csv_cell('SF123'), 'SF123')
        self.assertEqual(csv_cell(None), '')


if __name__ == '__main__':
    unittest.main()
