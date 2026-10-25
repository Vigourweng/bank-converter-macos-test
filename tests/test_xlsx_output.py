"""Independent OOXML and integration checks; every statement here is fictional."""
from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal
import os
from pathlib import Path
import posixpath
import shutil
import sys
import tempfile
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

sys.dont_write_bytecode = True
ROOT = Path(os.environ.get("BANK_TOOL_ROOT", Path(__file__).resolve().parents[1])).resolve()
sys.path.insert(0, str(ROOT))
import bank_merge as bank
import bank_xlsx as writer


HEADER = ["Date", "SERVICE FEE", "Amount", "DESCRIPTION", "REFERENCE", "Balance", "CHEQUE NUMBER"]
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
P = "http://schemas.openxmlformats.org/package/2006/relationships"


def serial_1900(value):
    """Compute Excel's serial independently, including its fictitious 1900 leap day."""
    elapsed = (value - date(1899, 12, 31)).days
    return elapsed + (1 if value >= date(1900, 3, 1) else 0)


def inline_text(cell):
    return "".join(node.text or "" for node in cell.findall(f"./{{{S}}}is//{{{S}}}t"))


def workbook_parts(path):
    with ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise AssertionError("XLSX ZIP CRC failed")
        names = set(archive.namelist())
        for required in ("[Content_Types].xml", "_rels/.rels", "xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl/styles.xml"):
            if required not in names:
                raise AssertionError(f"Required OOXML part missing: {required}")
        # XML is parsed directly, without using the production bank XLSX reader.
        for name in names:
            if name.endswith((".xml", ".rels")):
                ET.fromstring(archive.read(name))
        book = ET.fromstring(archive.read("xl/workbook.xml"))
        sheets = book.findall(f"./{{{S}}}sheets/{{{S}}}sheet")
        if len(sheets) != 1:
            raise AssertionError("Expected one import worksheet")
        relation_id = sheets[0].get(f"{{{R}}}id")
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = [node.get("Target") for node in rels.findall(f"{{{P}}}Relationship")
                   if node.get("Id") == relation_id]
        if len(targets) != 1:
            raise AssertionError("Workbook sheet relationship is missing or ambiguous")
        target = targets[0]
        worksheet_name = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
        sheet = ET.fromstring(archive.read(worksheet_name))
        styles = ET.fromstring(archive.read("xl/styles.xml"))
        raw_sheet = archive.read(worksheet_name)
    cells = {node.get("r"): node for node in sheet.findall(f"./{{{S}}}sheetData/{{{S}}}row/{{{S}}}c")}
    return book, sheet, styles, cells, raw_sheet


def input_rows(first="01/09/2026", last="30/09/2026", *, amount="200.00", description="SYNTHETIC RENT", reference="000123"):
    return [list(HEADER), [first] + [""] * 6,
            [first, "", amount, description, reference, "", ""], [last] + [""] * 6]


class XlsxOutputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="fictional-xlsx-")
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.sequence = 0

    def write(self, rows=None, filename=None):
        self.sequence += 1
        filename = filename or f"synthetic import-{self.sequence}.xlsx"
        path = self.folder / filename
        writer.write_import_xlsx(path, rows if rows is not None else input_rows())
        self.assertTrue(path.is_file())
        return path

    def assert_date_cell(self, cell, styles, expected):
        self.assertIn(cell.get("t", "n"), ("n",), "Date must be numeric, not a locale-dependent string")
        self.assertEqual(cell.findtext(f"{{{S}}}f"), f"DATE({expected.year},{expected.month},{expected.day})")
        self.assertEqual(Decimal(cell.findtext(f"{{{S}}}v")), Decimal(serial_1900(expected)))
        formats = {node.get("numFmtId"): node.get("formatCode")
                   for node in styles.findall(f"./{{{S}}}numFmts/{{{S}}}numFmt")}
        xfs = styles.findall(f"./{{{S}}}cellXfs/{{{S}}}xf")
        self.assertIsNotNone(cell.get("s"), "Date cell needs an explicit date style")
        style = xfs[int(cell.get("s"))]
        self.assertEqual(formats.get(style.get("numFmtId")), "dd/mm/yyyy")

    def assert_blank(self, cell):
        if cell is not None:
            self.assertIsNone(cell.find(f"{{{S}}}f"), "A blank boundary field must not contain a formula")
            self.assertIn(cell.findtext(f"{{{S}}}v"), (None, ""))
            self.assertEqual(inline_text(cell), "")

    def test_valid_package_and_canonical_header(self):
        self.assertEqual(bank.HEADER, HEADER)
        book, sheet, _, cells, _ = workbook_parts(self.write())
        self.assertEqual([inline_text(cells[f"{column}1"]) for column in "ABCDEFG"], HEADER)
        self.assertEqual(len(sheet.findall(f"./{{{S}}}sheetData/{{{S}}}row")), 4)
        properties = book.find(f"{{{S}}}workbookPr")
        if properties is not None:
            self.assertNotIn(properties.get("date1904", "0").lower(), ("1", "true"))

    def test_every_date_has_formula_cache_and_custom_display(self):
        _, _, styles, cells, _ = workbook_parts(self.write())
        for ref, expected in (("A2", date(2026, 9, 1)), ("A3", date(2026, 9, 1)), ("A4", date(2026, 9, 30))):
            with self.subTest(ref=ref):
                self.assert_date_cell(cells[ref], styles, expected)

    def test_leap_day_and_month_boundaries(self):
        transaction = bank.Transaction("fictional.csv", 2, "000123", datetime(2028, 2, 29), Decimal("12.34"), "LEAP RENT", "000007")
        rows = bank.import_rows([transaction], "2028-02")
        self.assertEqual(rows[1], ["01/02/2028"] + [""] * 6)
        self.assertEqual(rows[-1], ["29/02/2028"] + [""] * 6)
        _, _, styles, cells, _ = workbook_parts(self.write(rows))
        for ref, expected in (("A2", date(2028, 2, 1)), ("A3", date(2028, 2, 29)), ("A4", date(2028, 2, 29))):
            self.assert_date_cell(cells[ref], styles, expected)

    def test_cached_serial_handles_excel_1900_leap_year_quirk(self):
        for stamp, expected in (("01/01/1900", 1), ("28/02/1900", 59), ("01/03/1900", 61)):
            with self.subTest(date=stamp):
                _, _, _, cells, _ = workbook_parts(self.write(input_rows(stamp, stamp), f"epoch-{expected}.xlsx"))
                self.assertEqual(Decimal(cells["A3"].findtext(f"{{{S}}}v")), Decimal(expected))

    def test_boundary_rows_have_six_blank_fields(self):
        _, _, _, cells, _ = workbook_parts(self.write())
        for row in (2, 4):
            for column in "BCDEFG":
                with self.subTest(row=row, column=column):
                    self.assert_blank(cells.get(f"{column}{row}"))

    def test_amounts_are_numeric_exact_two_decimal_values(self):
        for value in ("0.10", "-0.01", "1234567.89", "0.00", Decimal("-500.20"), 42):
            with self.subTest(amount=value):
                _, _, _, cells, _ = workbook_parts(self.write(input_rows(amount=value)))
                cell = cells["C3"]
                self.assertEqual(cell.get("t", "n"), "n")
                self.assertIsNone(cell.find(f"{{{S}}}f"))
                self.assertIsNone(cell.find(f"{{{S}}}is"))
                value_text = cell.findtext(f"{{{S}}}v")
                self.assertRegex(value_text, r"^-?\d+\.\d{2}$")
                self.assertEqual(Decimal(value_text), Decimal(str(value)))

    def test_description_and_reference_are_literal_inline_strings(self):
        literals = ("=SUM(1,2)", "+00123", "-00042", "@fictional", "000000123456")
        rows = [list(HEADER), ["01/09/2026"] + [""] * 6]
        rows += [["02/09/2026", "", "1.00", value, value, "", ""] for value in literals]
        rows += [["30/09/2026"] + [""] * 6]
        _, _, _, cells, _ = workbook_parts(self.write(rows))
        for row, value in enumerate(literals, 3):
            for column in "DE":
                cell = cells[f"{column}{row}"]
                self.assertEqual(cell.get("t"), "inlineStr")
                self.assertIsNone(cell.find(f"{{{S}}}f"))
                self.assertEqual(inline_text(cell), value)

    def test_xml_escaping_unicode_and_whitespace_are_preserved(self):
        description = '  虚构租金 & <shop> "一号"\nsecond line\t中文  '
        reference = "0000123 & <ref> 'fictional'"
        _, _, _, cells, raw = workbook_parts(self.write(input_rows(description=description, reference=reference)))
        self.assertEqual(inline_text(cells["D3"]), description)
        self.assertEqual(inline_text(cells["E3"]), reference)
        self.assertIn(b"&amp;", raw)
        self.assertIn(b"&lt;", raw)

    def test_invalid_xml_controls_are_rejected_before_a_workbook_is_written(self):
        for control in ("\x00", "\x01", "\x0b", "\x0c", "\x1f", "\ufffe", "\uffff"):
            for column in (3, 4):
                with self.subTest(control=repr(control), column=column):
                    rows = input_rows()
                    rows[2][column] = "SYNTHETIC" + control + "TEXT"
                    path = self.folder / "invalid-control.xlsx"
                    with self.assertRaisesRegex(RuntimeError, r"(?i)XML|字符|control"):
                        writer.write_import_xlsx(path, rows)
                    self.assertFalse(path.exists(), "Invalid XML must not leave a seemingly usable workbook")

    def test_nonfinite_and_overprecision_amounts_are_not_silently_rounded(self):
        for value in ("1.001", Decimal("NaN"), Decimal("Infinity"), "not-money"):
            with self.subTest(amount=value):
                path = self.folder / "invalid-amount.xlsx"
                with self.assertRaises(RuntimeError):
                    writer.write_import_xlsx(path, input_rows(amount=value))
                self.assertFalse(path.exists())


class XlsxIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="fictional-xlsx-integration-")
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.source = self.folder / "original synthetic statement.csv"
        self.source.write_text(
            "Date,Description,Amount,Balance,Account Number\n"
            ",Opening balance,,1000,000123\n"
            "01/09/2026,SYNTHETIC RENT,200,1200,000123\n"
            "02/09/2026,SYNTHETIC PAYMENT,-50,1150,000123\n"
            ",Closing balance,,1150,000123\n", encoding="utf-8",
        )

    def generated(self):
        transactions, report = bank.prepare(files=[self.source], profiles=[])
        output = bank.write_result(transactions, report, self.folder / "generated")
        return output, bank.import_rows(transactions, "2026-09")

    def test_write_result_creates_sibling_csv_and_xlsx_with_matching_values(self):
        original = self.source.read_bytes()
        output, expected = self.generated()
        csv_path = output / "ERP_import_2026-09.csv"
        xlsx_path = output / "ERP_import_2026-09.xlsx"
        self.assertTrue(csv_path.is_file())
        self.assertTrue(xlsx_path.is_file())
        with csv_path.open(encoding="utf-8-sig", newline="") as handle:
            self.assertEqual(list(csv.reader(handle)), expected)
        _, _, styles, cells, _ = workbook_parts(xlsx_path)
        self.assertEqual([inline_text(cells[f"{column}1"]) for column in "ABCDEFG"], HEADER)
        for row_number, row in enumerate(expected[1:], 2):
            day, month, year = map(int, row[0].split("/"))
            self.assert_date_cell(cells[f"A{row_number}"], styles, date(year, month, day))
            for index, column in enumerate("BCDEFG", 1):
                cell = cells.get(f"{column}{row_number}")
                if row[index] == "":
                    XlsxOutputTests.assert_blank(self, cell)
                elif column == "C":
                    self.assertEqual(Decimal(cell.findtext(f"{{{S}}}v")), Decimal(row[index]))
                else:
                    self.assertEqual(inline_text(cell), row[index])
        self.assertEqual(self.source.read_bytes(), original)

    assert_date_cell = XlsxOutputTests.assert_date_cell

    def test_folder_selection_skips_renamed_generated_csv_and_xlsx_by_actual_header(self):
        output, _ = self.generated()
        generated_csv = self.folder / "ordinary looking download.csv"
        generated_xlsx = self.folder / "another bank download.xlsx"
        shutil.copyfile(output / "ERP_import_2026-09.csv", generated_csv)
        shutil.copyfile(output / "ERP_import_2026-09.xlsx", generated_xlsx)
        chosen, skipped = bank.choose_sources(self.folder)
        self.assertEqual(chosen, [self.source.resolve()])
        self.assertEqual(set(skipped), {generated_csv.name, generated_xlsx.name})
        transactions, report = bank.prepare(self.folder, profiles=[])
        self.assertEqual(len(transactions), 2)
        self.assertEqual(report["source_count"], 1)
        self.assertEqual(set(report["skipped_csv_files"]), {generated_csv.name, generated_xlsx.name})

    def test_explicit_generated_xlsx_selection_is_rejected_as_an_iq_product(self):
        output, _ = self.generated()
        disguised = self.folder / "renamed synthetic statement.xlsx"
        shutil.copyfile(output / "ERP_import_2026-09.xlsx", disguised)
        with self.assertRaisesRegex(bank.InputError, r"IQ|成品|导入文件"):
            bank.parse_source(disguised, profiles=[])

    def test_output_guard_does_not_silently_skip_a_corrupt_workbook(self):
        broken = self.folder / "broken synthetic statement.xlsx"
        broken.write_bytes(b"PK\x03\x04truncated synthetic workbook")
        with self.assertRaisesRegex(bank.InputError, r"Excel|读取"):
            bank.choose_sources(self.folder)
        with self.assertRaisesRegex(bank.InputError, r"Excel|读取"):
            bank.parse_source(broken, profiles=[])


if __name__ == "__main__":
    unittest.main(verbosity=2)
