"""Write the IQ import layout as portable OOXML using only the standard library."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
import re
import zipfile
from xml.etree import ElementTree as ET


HEADER = ('Date', 'SERVICE FEE', 'Amount', 'DESCRIPTION', 'REFERENCE', 'Balance', 'CHEQUE NUMBER')
MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PACKAGE_REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
CONTENT = 'http://schemas.openxmlformats.org/package/2006/content-types'


class XlsxExportError(RuntimeError):
    """The input cannot be exported without changing its values."""


def _xml(node):
    # A literal CR would be normalized to LF by XML readers. Character references
    # preserve legitimate CR, LF and tabs without removing source text.
    return ET.tostring(node, encoding='utf-8', xml_declaration=True).replace(b'\r', b'&#13;')


def _text(value, label):
    text = '' if value is None else str(value)
    for character in text:
        point = ord(character)
        if not (point in (9, 10, 13) or 0x20 <= point <= 0xD7FF
                or 0xE000 <= point <= 0xFFFD or 0x10000 <= point <= 0x10FFFF):
            raise XlsxExportError(f'{label} 含 XML/Excel 不支持的字符 U+{point:04X}；未删除或替换原文')
    if len(text.encode('utf-16-le')) // 2 > 32767:
        raise XlsxExportError(f'{label} 超过 Excel 单元格 32767 字符限制；未截断原文')
    # OOXML reserves these escape-looking sequences. Escape their underscore so
    # a literal reference such as _x0041_ does not become "A" in Excel.
    return re.sub(r'_(x[0-9A-Fa-f]{4}_)', r'_x005F_\1', text)


def _serial(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{2}/\d{2}/\d{4}', value):
        raise XlsxExportError('XLSX 日期必须是明确的 DD/MM/YYYY 日期')
    try:
        day = datetime.strptime(value, '%d/%m/%Y').date()
    except ValueError as error:
        raise XlsxExportError(f'XLSX 日期无效：{value!r}') from error
    if day.year < 1900:
        raise XlsxExportError('Excel DATE 公式不支持本工具导出 1900 年以前的日期；未更改日期')
    serial = (day - date(1899, 12, 31)).days
    if day >= date(1900, 3, 1):
        serial += 1  # Excel's 1900 date system includes its historical leap-day bug.
    return day, serial


def _amount(value, label):
    if isinstance(value, (bool, float)):
        raise XlsxExportError(f'{label} 必须使用精确的 Decimal 或金额文字，不能使用浮点近似值')
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
        if not number.is_finite() or abs(number) >= Decimal('1e308'):
            raise XlsxExportError(f'{label} 超出 Excel 可保存的有限金额范围')
        with localcontext() as context:
            context.prec = max(28, len(number.as_tuple().digits) + max(number.as_tuple().exponent, 0) + 3)
            rounded = number.quantize(Decimal('.01'))
        if number != rounded:
            raise XlsxExportError(f'{label} 不是精确到分的金额；未四舍五入')
        significant = list(number.as_tuple().digits)
        while significant and significant[-1] == 0:
            significant.pop()
        if len(significant) > 15:
            raise XlsxExportError(f'{label} 超过 Excel 的 15 位有效数字限制；未降低金额精度')
        return format(rounded, '.2f')
    except (InvalidOperation, ValueError) as error:
        raise XlsxExportError(f'{label} 不是有效的精确金额') from error


def _inline(parent, address, value, style=0):
    cell = ET.SubElement(parent, 'c', {'r': address, 't': 'inlineStr', 's': str(style)})
    inline = ET.SubElement(cell, 'is')
    ET.SubElement(inline, 't', {'{http://www.w3.org/XML/1998/namespace}space': 'preserve'}).text = _text(value, address)


def _sheet(rows):
    rows = [list(row) for row in rows]
    if not rows or tuple(rows[0]) != HEADER or any(len(row) != 7 for row in rows):
        raise XlsxExportError('XLSX 输出必须与 IQ 的七列表头及行结构一致')
    if len(rows) < 3 or len(rows) > 1048576:
        raise XlsxExportError('XLSX 行数无效或超过 Excel 的单工作表行数限制')
    if any(value not in ('', None) for row in (rows[1], rows[-1]) for value in row[1:]):
        raise XlsxExportError('XLSX 月初和月末日期行的其余六列必须留空')
    root = ET.Element('worksheet', {'xmlns': MAIN})
    ET.SubElement(root, 'dimension', {'ref': f'A1:G{len(rows)}'})
    views = ET.SubElement(root, 'sheetViews')
    view = ET.SubElement(views, 'sheetView', {'workbookViewId': '0'})
    ET.SubElement(view, 'pane', {'ySplit': '1', 'topLeftCell': 'A2', 'activePane': 'bottomLeft', 'state': 'frozen'})
    ET.SubElement(root, 'sheetFormatPr', {'defaultRowHeight': '15'})
    columns = ET.SubElement(root, 'cols')
    for i, width in enumerate((15, 16, 18, 52, 32, 16, 20), 1):
        ET.SubElement(columns, 'col', {'min': str(i), 'max': str(i), 'width': str(width), 'customWidth': '1'})
    data = ET.SubElement(root, 'sheetData')
    for index, values in enumerate(rows, 1):
        row = ET.SubElement(data, 'row', {'r': str(index)})
        for column, value in enumerate(values):
            address = chr(65 + column) + str(index)
            if index == 1:
                _inline(row, address, value, 1)
            elif column == 0:
                day, serial = _serial(value)
                cell = ET.SubElement(row, 'c', {'r': address, 's': '2', 't': 'n'})
                ET.SubElement(cell, 'f').text = f'DATE({day.year},{day.month},{day.day})'
                ET.SubElement(cell, 'v').text = str(serial)
            elif value not in ('', None):
                if column == 2:
                    cell = ET.SubElement(row, 'c', {'r': address, 's': '3', 't': 'n'})
                    ET.SubElement(cell, 'v').text = _amount(value, address)
                else:
                    _inline(row, address, value)
    return _xml(root)


def _styles():
    root = ET.Element('styleSheet', {'xmlns': MAIN})
    formats = ET.SubElement(root, 'numFmts', {'count': '2'})
    ET.SubElement(formats, 'numFmt', {'numFmtId': '164', 'formatCode': 'dd/mm/yyyy'})
    ET.SubElement(formats, 'numFmt', {'numFmtId': '165', 'formatCode': '0.00'})
    fonts = ET.SubElement(root, 'fonts', {'count': '2'})
    for bold in (False, True):
        font = ET.SubElement(fonts, 'font')
        if bold:
            ET.SubElement(font, 'b')
        ET.SubElement(font, 'sz', {'val': '11'})
        ET.SubElement(font, 'name', {'val': 'Calibri'})
    fills = ET.SubElement(root, 'fills', {'count': '2'})
    for pattern in ('none', 'gray125'):
        ET.SubElement(ET.SubElement(fills, 'fill'), 'patternFill', {'patternType': pattern})
    borders = ET.SubElement(root, 'borders', {'count': '1'})
    border = ET.SubElement(borders, 'border')
    for edge in ('left', 'right', 'top', 'bottom', 'diagonal'):
        ET.SubElement(border, edge)
    base = {'numFmtId': '0', 'fontId': '0', 'fillId': '0', 'borderId': '0'}
    ET.SubElement(ET.SubElement(root, 'cellStyleXfs', {'count': '1'}), 'xf', base)
    cell_formats = ET.SubElement(root, 'cellXfs', {'count': '4'})
    for number, font in ((0, 0), (0, 1), (164, 0), (165, 0)):
        attrs = dict(base, numFmtId=str(number), fontId=str(font), xfId='0')
        if number:
            attrs['applyNumberFormat'] = '1'
        if font:
            attrs['applyFont'] = '1'
        ET.SubElement(cell_formats, 'xf', attrs)
    ET.SubElement(ET.SubElement(root, 'cellStyles', {'count': '1'}), 'cellStyle', {'name': 'Normal', 'xfId': '0', 'builtinId': '0'})
    return _xml(root)


def write_import_xlsx(path, rows):
    """Write one IQ worksheet, retaining literal text and cached DATE results."""
    sheet = _sheet(rows)  # Validate all content before creating any output ZIP.
    types = ET.Element('Types', {'xmlns': CONTENT})
    ET.SubElement(types, 'Default', {'Extension': 'rels', 'ContentType': 'application/vnd.openxmlformats-package.relationships+xml'})
    ET.SubElement(types, 'Default', {'Extension': 'xml', 'ContentType': 'application/xml'})
    for part, kind in (('/xl/workbook.xml', 'sheet.main'), ('/xl/worksheets/sheet1.xml', 'worksheet'), ('/xl/styles.xml', 'styles')):
        ET.SubElement(types, 'Override', {'PartName': part, 'ContentType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.' + kind + '+xml'})
    root_rels = ET.Element('Relationships', {'xmlns': PACKAGE_REL})
    ET.SubElement(root_rels, 'Relationship', {'Id': 'rId1', 'Type': REL + '/officeDocument', 'Target': 'xl/workbook.xml'})
    workbook = ET.Element('workbook', {'xmlns': MAIN, 'xmlns:r': REL})
    ET.SubElement(workbook, 'workbookPr', {'date1904': '0'})
    ET.SubElement(ET.SubElement(workbook, 'sheets'), 'sheet', {'name': 'IQ Import', 'sheetId': '1', 'r:id': 'rId1'})
    ET.SubElement(workbook, 'calcPr', {'calcId': '191029', 'calcMode': 'auto', 'fullCalcOnLoad': '1', 'forceFullCalc': '1'})
    book_rels = ET.Element('Relationships', {'xmlns': PACKAGE_REL})
    for identifier, kind, target in (('rId1', 'worksheet', 'worksheets/sheet1.xml'), ('rId2', 'styles', 'styles.xml')):
        ET.SubElement(book_rels, 'Relationship', {'Id': identifier, 'Type': REL + '/' + kind, 'Target': target})
    parts = {'[Content_Types].xml': _xml(types), '_rels/.rels': _xml(root_rels),
             'xl/workbook.xml': _xml(workbook), 'xl/_rels/workbook.xml.rels': _xml(book_rels),
             'xl/styles.xml': _styles(), 'xl/worksheets/sheet1.xml': sheet}
    path = Path(path)
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            entry = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, data)
    return path
