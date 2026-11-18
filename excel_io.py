# -*- coding: utf-8 -*-
"""极简 xlsx 读写：只用标准库（zipfile + ElementTree），不引入第三方依赖。

为什么不用 openpyxl / pandas：程序是单文件 exe，多一个第三方库就多几 MB 体积
和启动解压时间；而导入场景只需要「读第一个工作表」和「生成模板」两件事，
手写一个最小实现足够，也不会因为打包漏模块而在用户机器上报 ImportError。

读（read_table）：
- 支持 Excel / WPS 默认的共享字符串（sharedStrings）、内联字符串、数字、
  布尔和公式缓存值；空单元格跳过，按单元格引用（如 C7）定位列，中间空列
  不会让后面的数据整体左移。
- 返回 [(行号, [单元格值...])]，行号是表格里的真实行号（含表头），报错时
  可以直接告诉用户「第 5 行」。
- .csv 用标准库 csv 读，兼容 UTF-8(BOM)、GBK、UTF-16 三种编码。
- 老式 .xls（Excel 97-2003 的二进制格式）标准库读不了，直接给出「请另存为
  .xlsx」的提示，而不是抛一个用户看不懂的异常。

写（write_xlsx）：只用来生成导入模板。全部写成内联字符串，首行加粗，
按需附一个「填写说明」工作表；生成的文件 Excel、WPS 都能正常打开。
"""

import csv
import os
import re
import zipfile
from xml.etree import ElementTree as ET

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"

XLSX_SUFFIXES = (".xlsx", ".xlsm")
CSV_SUFFIXES = (".csv", ".txt")

# 文件选择框用
FILE_TYPES = (
    ("Excel 工作簿", "*.xlsx *.xlsm"),
    ("CSV 文件", "*.csv"),
    ("所有文件", "*.*"),
)


class ExcelReadError(Exception):
    """文件读不了时抛这个，message 直接展示给用户。"""


def _q(tag):
    return "{%s}%s" % (NS_MAIN, tag)


def _col_letter(index):
    """0 → A、25 → Z、26 → AA。"""
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _col_index(ref):
    """单元格引用 'AB12' → 27（0 基）；取不到返回 None。"""
    match = re.match(r"([A-Za-z]+)", ref or "")
    if not match:
        return None
    index = 0
    for ch in match.group(1).upper():
        index = index * 26 + (ord(ch) - 64)
    return index - 1


def _number(text):
    """数字文本 → int/float；不是数字就原样返回。"""
    try:
        value = float(text)
    except (TypeError, ValueError):
        return text
    return int(value) if value == int(value) else value


def _shared_strings(archive):
    try:
        data = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    out = []
    for item in root.iter(_q("si")):
        # 富文本会把一段话拆成多个 <t>，拼起来才是完整内容
        out.append("".join(node.text or "" for node in item.iter(_q("t"))))
    return out


def _first_sheet(archive):
    """返回 (工作表名, 包内路径)。取不到就按 sheet1 兜底。"""
    name, rel_id = "Sheet1", None
    try:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        for sheet in workbook.iter(_q("sheet")):
            name = sheet.get("name") or name
            rel_id = sheet.get("{%s}id" % NS_DOC_REL)
            break
    except (KeyError, ET.ParseError):
        pass

    target = None
    try:
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        for rel in rels:
            if rel_id and rel.get("Id") == rel_id:
                target = rel.get("Target")
                break
    except (KeyError, ET.ParseError):
        pass

    target = (target or "worksheets/sheet1.xml").lstrip("/")
    if not target.startswith("xl/"):
        target = "xl/" + target
    return name, target


def read_xlsx(path):
    """读第一个工作表，返回 ([(行号, [值...])], 工作表名)。"""
    if not os.path.isfile(path):
        raise ExcelReadError("文件不存在：%s" % path)
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise ExcelReadError(
            "这个文件不是有效的 .xlsx（老版 Excel 的 .xls 请先「另存为」.xlsx）")

    with archive:
        shared = _shared_strings(archive)
        sheet_name, sheet_path = _first_sheet(archive)
        try:
            data = archive.read(sheet_path)
        except KeyError:
            raise ExcelReadError("工作簿里没有可读的工作表")

    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise ExcelReadError("工作表内容损坏，无法解析")

    rows = []
    for row in root.iter(_q("row")):
        try:
            line = int(row.get("r") or (len(rows) + 1))
        except ValueError:
            line = len(rows) + 1
        cells = {}
        cursor = 0
        for cell in row.iter(_q("c")):
            ref = cell.get("r")
            index = _col_index(ref) if ref else cursor
            if index is None:
                index = cursor
            cursor = index + 1

            kind = cell.get("t") or "n"
            value = None
            if kind == "inlineStr":
                value = "".join(node.text or "" for node in cell.iter(_q("t")))
            else:
                raw = None
                for node in cell.iter(_q("v")):
                    raw = node.text
                    break
                if raw is None:
                    value = None
                elif kind == "s":
                    try:
                        value = shared[int(raw)]
                    except (ValueError, IndexError):
                        value = ""
                elif kind in ("str", "e"):
                    value = raw
                elif kind == "b":
                    value = "TRUE" if raw.strip() == "1" else "FALSE"
                else:
                    value = _number(raw)
            if value is None or value == "":
                continue
            cells[index] = value

        if not cells:
            continue
        width = max(cells) + 1
        rows.append((line, [cells.get(i) for i in range(width)]))
    return rows, sheet_name


def read_csv(path):
    """读 csv，兼容 Excel 导出的三种常见编码。"""
    last_error = None
    for encoding in ("utf-8-sig", "gbk", "utf-16"):
        try:
            with open(path, "r", encoding=encoding, newline="") as handle:
                reader = csv.reader(handle)
                rows = []
                for line, row in enumerate(reader, 1):
                    cells = [str(cell).strip() for cell in row]
                    if not any(cells):
                        continue
                    rows.append((line, cells))
            return rows, "CSV"
        except UnicodeDecodeError as e:
            last_error = e
            continue
        except OSError as e:
            raise ExcelReadError("读取失败：%s" % e)
    raise ExcelReadError("CSV 编码无法识别（%s），请用 Excel 另存为 .xlsx 再导入"
                         % last_error)


def read_table(path):
    """按扩展名选择读法，返回 ([(行号, [值...])], 工作表名)。"""
    suffix = os.path.splitext(path or "")[1].lower()
    if suffix in XLSX_SUFFIXES:
        return read_xlsx(path)
    if suffix in CSV_SUFFIXES:
        return read_csv(path)
    if suffix == ".xls":
        raise ExcelReadError(
            "不支持 .xls（Excel 97-2003 格式）。请用 Excel 打开后"
            "「另存为」.xlsx，再重新导入")
    raise ExcelReadError("不支持的文件类型「%s」，请使用 .xlsx 或 .csv"
                         % (suffix or "无扩展名"))


# ---------------------------------------------------------------- 写模板


def _xml_escape(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _cell_xml(ref, value, style=None):
    if value is None or value == "":
        return ""
    attr = ' s="%d"' % style if style else ""
    if isinstance(value, bool):
        return '<c r="%s"%s t="inlineStr"><is><t>%s</t></is></c>' % (
            ref, attr, "TRUE" if value else "FALSE")
    if isinstance(value, (int, float)):
        return '<c r="%s"%s><v>%s</v></c>' % (ref, attr, value)
    return ('<c r="%s"%s t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>'
            % (ref, attr, _xml_escape(value)))


def _sheet_xml(rows, widths=None):
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             '<worksheet xmlns="%s">' % NS_MAIN]
    if widths:
        parts.append("<cols>")
        for index, width in enumerate(widths, 1):
            parts.append('<col min="%d" max="%d" width="%s" customWidth="1"/>'
                         % (index, index, width))
        parts.append("</cols>")
    parts.append("<sheetData>")
    for row_no, row in enumerate(rows or [], 1):
        cells = []
        for col_no, value in enumerate(row or [], 1):
            cell = _cell_xml("%s%d" % (_col_letter(col_no - 1), row_no), value,
                             1 if row_no == 1 else None)
            if cell:
                cells.append(cell)
        parts.append('<row r="%d">%s</row>' % (row_no, "".join(cells)))
    parts.append("</sheetData></worksheet>")
    return "".join(parts)


_STYLES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="%s">
<fonts count="2">
  <font><sz val="11"/><name val="微软雅黑"/></font>
  <font><b/><sz val="11"/><name val="微软雅黑"/></font>
</fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill>
<fill><patternFill patternType="gray125"/></fill></fills>
<borders count="1"><border/></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="2">
  <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
  <xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>
</cellXfs>
</styleSheet>""" % NS_MAIN


def write_xlsx(path, rows, widths=None, notes=None, sheet_name="Sheet1",
               notes_sheet="填写说明"):
    """写一个最小可用的 xlsx：首行加粗，可选「填写说明」工作表。"""
    sheets = [(sheet_name, [list(r) for r in (rows or [])], widths)]
    if notes:
        sheets.append((notes_sheet, [list(n) for n in notes], None))

    sheet_overrides = "".join(
        '<Override PartName="/xl/worksheets/sheet%d.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument'
        '.spreadsheetml.worksheet+xml"/>' % (i + 1) for i in range(len(sheets)))
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
%s
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>""" % sheet_overrides

    root_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="%s">
<Relationship Id="rId1" Type="%s/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""" % (NS_PKG_REL, NS_DOC_REL)

    sheet_tags = "".join(
        '<sheet name="%s" sheetId="%d" r:id="rId%d"/>'
        % (_xml_escape(name), i + 1, i + 1) for i, (name, _r, _w) in enumerate(sheets))
    workbook = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="%s" xmlns:r="%s"><sheets>%s</sheets></workbook>""" % (
        NS_MAIN, NS_DOC_REL, sheet_tags)

    rel_items = "".join(
        '<Relationship Id="rId%d" Type="%s/worksheet" Target="worksheets/sheet%d.xml"/>'
        % (i + 1, NS_DOC_REL, i + 1) for i in range(len(sheets)))
    rel_items += ('<Relationship Id="rId%d" Type="%s/styles" Target="styles.xml"/>'
                  % (len(sheets) + 1, NS_DOC_REL))
    workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="%s">%s</Relationships>""" % (NS_PKG_REL, rel_items)

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/styles.xml", _STYLES_XML)
        for index, (_name, sheet_rows, sheet_widths) in enumerate(sheets, 1):
            archive.writestr("xl/worksheets/sheet%d.xml" % index,
                             _sheet_xml(sheet_rows, sheet_widths))
    return path
