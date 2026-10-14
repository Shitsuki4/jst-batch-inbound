"""Offline report clipboard tests. Never writes the system clipboard."""
import base64
import ctypes
import json
import os
from pathlib import Path
import re
import struct
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch
import zlib
import report_clipboard as m


def png(width=2,height=2):
    def chunk(kind,data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    raw=(b'\0'+b'\x20\x40\x80\xff'*width)*height
    return m.PNG_SIGNATURE+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')

class ClipboardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='jst-clipboard-unit-')
        self.root=Path(self.temp.name)
        self.writes=[]
        self.service=m.ReportClipboard(self.root/'cache',writer=lambda h,t:self.writes.append((h,t)))
        self.service.supported=True
        self.payload={'token':self.service.token,'text':'采购单：测试<&>\n外部：A\n入库：RK-1、RK-2','png':'data:image/png;base64,'+base64.b64encode(png()).decode()}
    def tearDown(self):self.temp.cleanup()
    def test_unicode_cfhtml_byte_offsets(self):
        data=m.cf_html('<div>中文🙂&amp;\n<img src="file:///C:/test.png"></div>')
        offsets={k:int(v) for k,v in re.findall(rb'(StartHTML|EndHTML|StartFragment|EndFragment):(\d+)',data)}
        self.assertEqual(data[offsets[b'StartFragment']:offsets[b'EndFragment']].decode(),'<div>中文🙂&amp;\n<img src="file:///C:/test.png"></div>')
        self.assertTrue(data[offsets[b'StartHTML']:offsets[b'EndHTML']].startswith(b'<html>'))
        self.assertEqual(data[offsets[b'EndHTML']:],b'\0')
    def test_copy_local_file_escape_no_bitmap(self):
        result=self.service.copy(self.payload)
        self.assertEqual(result['transport'],'windows-cf-html')
        self.assertEqual((result['width'],result['height']),(2,2))
        h,t=self.writes[0];body=h.decode('utf-8')
        self.assertEqual(t,self.payload['text'])
        self.assertIn('测试&lt;&amp;&gt;<br>',body)
        self.assertIn('src="file:///',body)
        self.assertNotIn('data:image',body)
        self.assertEqual(len(list(self.service.directory.glob('*.png'))),1)
        self.assertEqual(next(self.service.directory.glob('*.png')).read_bytes(),png())
    def test_multiple_copies_immutable(self):
        self.service.copy(self.payload);self.service.copy(self.payload)
        self.assertEqual(len(list(self.service.directory.glob('*.png'))),2)
        self.assertNotEqual(self.writes[0][0],self.writes[1][0])
    def test_wrong_or_missing_token_no_write(self):
        for token in ('wrong',None,1):
            with self.assertRaises(PermissionError):self.service.copy(dict(self.payload,token=token))
        self.assertFalse(self.service.directory.exists());self.assertEqual(self.writes,[])
    def test_bad_text_no_write(self):
        for text in ('','  ',None,[], 'a\0b','x'*(m.MAX_TEXT_CHARS+1)):
            with self.assertRaises(ValueError):self.service.copy(dict(self.payload,text=text))
        self.assertEqual(self.writes,[])
    def test_bad_base64_and_non_png(self):
        for data in ('data:image/png;base64,!','data:image/jpeg;base64,AA==','data:image/png;base64,YQ=='):
            with self.assertRaises(ValueError):self.service.copy(dict(self.payload,png=data))
    def test_png_corruption_and_truncation(self):
        data=png()
        for value in (data[:-1],data+b'ignored',data[:40]+b'X'+data[41:]):
            with self.assertRaises(ValueError):m.validate_png(value)
    def test_png_dimension_limit(self):
        for width,height in ((4097,1),(1,30001),(4000,11000)):
            with self.assertRaises(ValueError):m.validate_png(png(width,height))
    def test_oversize_data_before_file(self):
        with self.assertRaises(ValueError):self.service.copy(dict(self.payload,png='data:image/png;base64,'+'A'*m.MAX_REQUEST_BYTES))
        self.assertFalse(self.service.directory.exists())
    def test_failure_reported_no_false_success(self):
        self.service.writer=lambda *a:(_ for _ in ()).throw(OSError('busy'))
        with self.assertRaisesRegex(OSError,'busy'):self.service.copy(self.payload)
    def test_cleanup_only_own_old_files(self):
        self.service.directory.mkdir()
        old=self.service.directory/('report-'+'a'*32+'.png');old.write_bytes(png())
        keep=self.service.directory/'my-report.png';keep.write_bytes(png())
        recent=self.service.directory/('report-'+'b'*32+'.png');recent.write_bytes(png())
        for f in (old,keep):os.utime(f,(1,1))
        self.service.copy(self.payload)
        self.assertFalse(old.exists());self.assertTrue(keep.exists());self.assertTrue(recent.exists())
    def test_unsupported_no_files(self):
        self.service.supported=False
        self.assertEqual(self.service.status()['token'],'')
        with self.assertRaises(ValueError):self.service.copy(self.payload)
        self.assertFalse(self.service.directory.exists())

class WindowsMemoryTests(unittest.TestCase):
    def fake(self,fail_set=False,busy=False):
        u,k=MagicMock(),MagicMock();self.buffers={}
        u.RegisterClipboardFormatW.return_value=50001
        u.OpenClipboard.return_value=not busy
        u.EmptyClipboard.return_value=True
        u.SetClipboardData.side_effect=[101,0] if fail_set else lambda kind,h:h
        def alloc(flags,size):
            key=101+len(self.buffers);self.buffers[key]=ctypes.create_string_buffer(size);return key
        k.GlobalAlloc.side_effect=alloc
        k.GlobalLock.side_effect=lambda h:ctypes.addressof(self.buffers[h])
        return u,k
    def test_transfer_format_order_and_ownership(self):
        u,k=self.fake()
        with patch.object(m.ctypes,'WinDLL',side_effect=[u,k],create=True),patch.object(m.os,'name','nt'):
            m.write_windows_clipboard(b'html\0','中文')
        self.assertEqual([x.args[0] for x in u.SetClipboardData.call_args_list],[50001,13])
        k.GlobalFree.assert_not_called();u.CloseClipboard.assert_called_once()
        self.assertEqual(self.buffers[102].raw,('中文\0').encode('utf-16-le'))
    def test_partial_failure_frees_only_untransferred(self):
        u,k=self.fake(fail_set=True)
        with patch.object(m.ctypes,'WinDLL',side_effect=[u,k],create=True),patch.object(m.os,'name','nt'):
            with self.assertRaises(OSError):m.write_windows_clipboard(b'html\0','test')
        k.GlobalFree.assert_called_once_with(102)
        self.assertEqual(u.EmptyClipboard.call_count,2)
        u.CloseClipboard.assert_called_once()
    def test_busy_retries_without_clearing(self):
        u,k=self.fake(busy=True)
        with patch.object(m.ctypes,'WinDLL',side_effect=[u,k],create=True),patch.object(m.os,'name','nt'),patch.object(m.time,'sleep'):
            with self.assertRaisesRegex(OSError,'占用'):m.write_windows_clipboard(b'html\0','test')
        self.assertEqual(u.OpenClipboard.call_count,10)
        u.EmptyClipboard.assert_not_called();self.assertEqual(k.GlobalFree.call_count,2)

if __name__=='__main__':unittest.main(verbosity=2)
