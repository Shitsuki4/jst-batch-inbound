# -*- coding: utf-8 -*-
"""极简 XLSX 写出器（zipfile + inlineStr，零第三方依赖）。"""
import zipfile
from xml.sax.saxutils import escape

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

_WORKBOOK = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>"""

_WB_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

# s="1" = 表头加粗；s="0" = 常规；s="2" = 文本格式(@)顶端对齐；s="3" = 文本格式(@)顶端对齐 + 自动换行
_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2"><font><sz val="11"/><name val="\u5fae\u8f6f\u96c5\u9ed1"/></font>
<font><b/><sz val="11"/><name val="\u5fae\u8f6f\u96c5\u9ed1"/></font></fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="4">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment vertical="top"/></xf>
<xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
</cellXfs>
</styleSheet>"""


def col_letter(n):
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _text_width(s):
    """显示宽度（字符单位）：中日韩 / 全角字符按 2，其余按 1。"""
    return sum(2 if ord(ch) > 0x2E7F else 1 for ch in s)


def _row_height(row, wrap_cols, widths, line_pt=17.0):
    """估算换行单元格的行高（磅）。多行明细按行数 + 超出列宽的自动折行计算，避免 WPS 不自动调整行高时被截断。"""
    lines = 1
    for ci in wrap_cols:
        if ci > len(row) or row[ci - 1] in (None, ''):
            continue
        width = max(4, float(widths.get(ci, 8.43)) - 1)
        n = 0
        for part in str(row[ci - 1]).split('\n'):
            n += max(1, -(-_text_width(part) // int(width)))
        lines = max(lines, n)
    return None if lines <= 1 else round(lines * line_pt + 3, 1)


def write_xlsx(path, sheet_name, header, rows, widths=None, wrap_cols=None, text_cols=None, freeze_header=False):
    """
    header: [str]；rows: [[值]]。所有单元格按文本写出（数量列也写文本，避免精度/格式歧义）。
    path 可以是文件路径或可写的二进制文件对象（如 io.BytesIO）。
    可选（不传时输出与旧版完全相同）：
      widths：{列号(从1起): 列宽(字符)}；wrap_cols：需自动换行的列号集合（多行单元格在 Excel / WPS 中换行显示，
      并按行数估算行高）；text_cols：设为文本格式(@)的列号集合（长单号不会变成 1.23E+14）；freeze_header：冻结首行。
    """
    wrap_cols = set(wrap_cols or ())
    text_cols = set(text_cols or ()) | wrap_cols
    widths = dict(widths or {})
    sheet_name = (sheet_name or "Sheet1")[:31] or "Sheet1"
    wb = _WORKBOOK.replace('name="Sheet1"', 'name="%s"' % escape(sheet_name))

    def cell(ref, val, style):
        style_attr = ' s="%d"' % style
        if val is None:
            val = ""
        if isinstance(val, (int, float)):
            return '<c r="%s"%s><v>%s</v></c>' % (ref, style_attr, val)
        txt = escape(str(val))
        return '<c r="%s"%s t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (ref, style_attr, txt)

    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">']
    if freeze_header:
        parts.append('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>')
    if widths:
        parts.append('<cols>' + ''.join('<col min="%d" max="%d" width="%s" customWidth="1"/>' % (c, c, widths[c]) for c in sorted(widths)) + '</cols>')
    parts.append('<sheetData>')
    parts.append('<row r="1">')
    for i, h in enumerate(header, 1):
        parts.append(cell("%s1" % col_letter(i), h, 1))
    parts.append('</row>')
    for ri, row in enumerate(rows, 2):
        height = _row_height(row, wrap_cols, widths) if wrap_cols else None
        parts.append('<row r="%d" ht="%s" customHeight="1">' % (ri, height) if height else '<row r="%d">' % ri)
        for ci, v in enumerate(row, 1):
            style = 3 if ci in wrap_cols else 2 if ci in text_cols else 0
            if ci in text_cols and isinstance(v, (int, float)):
                v = str(v)
            parts.append(cell("%s%d" % (col_letter(ci), ri), v, style))
        parts.append('</row>')
    parts.append('</sheetData></worksheet>')
    sheet = "".join(parts)

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", _WB_RELS)
        z.writestr("xl/styles.xml", _STYLES)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return path
