"""Fictional FNB Stmnt3 regressions; no customer statements or external libraries."""
import csv
from datetime import datetime
from decimal import Decimal
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(os.environ.get('BANK_TOOL_ROOT', Path(__file__).resolve().parents[1])).resolve()
sys.path.insert(0, str(ROOT))
import bank_formats as fmt


ACCOUNT = '0000000000000123'
BRANCH = '00000100'


def record(fields):
    result = [' '] * 250
    for start, end, value in fields:
        if len(value) != end - start + 1:
            raise AssertionError((start, end, value))
        result[start - 1:end] = value
    return ''.join(result)


def header(opening=100000, closing=115000, credits=1, credit_total=20000,
           debits=1, debit_total=5000, statement='00001', statement_date='20260930', prior='20260831'):
    return record([
        (1, 3, '015'), (4, 6, 'CUR'), (7, 14, BRANCH), (15, 30, ACCOUNT),
        (31, 38, '20260930'), (39, 43, '00000'), (44, 44, 'H'),
        (45, 64, 'SYNTHETIC PRODUCT'.ljust(20)), (65, 72, statement_date), (73, 80, prior),
        (81, 93, f'{abs(opening):013d}'), (94, 94, 'D' if opening < 0 else 'C'),
        (95, 107, f'{abs(closing):013d}'), (108, 108, 'D' if closing < 0 else 'C'),
        (109, 113, f'{credits:05d}'), (114, 126, f'{credit_total:013d}'),
        (127, 131, f'{debits:05d}'), (132, 144, f'{debit_total:013d}'),
        (145, 149, statement), (150, 150, 'N'), (178, 217, 'FICTIONAL TEST ACCOUNT'.ljust(40)),
        (241, 243, 'ZAR'), (250, 250, '3'),
    ])


def transaction(sequence, amount, date='20260901', fee=0, fee_indicator=' ',
                description='SYNTHETIC TRANSACTION', reference='SYNTHETIC REF', narrative='', cheque=0):
    return record([
        (1, 3, '015'), (4, 6, 'CUR'), (7, 14, BRANCH), (15, 30, ACCOUNT),
        (31, 38, '20260930'), (39, 43, f'{sequence:05d}'), (44, 44, 'T'),
        (45, 52, date), (53, 60, date), (61, 65, '00001'), (66, 67, '00'),
        (68, 80, f'{abs(amount):013d}'), (81, 81, 'D' if amount < 0 else 'C'),
        (93, 98, f'{sequence:06d}'), (99, 105, f'{fee:07d}'), (106, 106, fee_indicator),
        (107, 118, f'{cheque:012d}'), (119, 165, narrative.ljust(47)),
        (178, 219, description.ljust(42)), (220, 249, reference.ljust(30)), (250, 250, '3'),
    ])


def replace_field(value, start, end, text):
    assert len(text) == end - start + 1
    return value[:start - 1] + text + value[end:]


class FnbStmt3Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.rows = [header(), transaction(1, 20000, fee=300, fee_indicator='D', description='RENT'),
                     transaction(2, -5000, '20260902', description='PAYMENT')]

    def write(self, rows=None, name='fictional.stmt3', newline='\n'):
        path = self.folder / name
        path.write_bytes((newline.join(rows or self.rows) + newline).encode('ascii'))
        return path

    def parse(self, rows=None):
        return fmt.fnb_stmt3_statement(self.write(rows))

    def reject(self, rows):
        with self.assertRaises(fmt.FormatError):
            self.parse(rows)

    def test_valid_implied_cents_and_header_totals(self):
        entries, summary = self.parse()
        self.assertEqual([e['amount'] for e in entries], [Decimal('200.00'), Decimal('-50.00')])
        self.assertEqual(entries[0]['date'], datetime(2026, 9, 1))
        self.assertEqual([e['source_row'] for e in entries], [2, 3])
        self.assertEqual(summary['account'], ACCOUNT)
        self.assertEqual(summary['currency'], 'ZAR')
        self.assertEqual(summary['opening'], '1000.00')
        self.assertEqual(summary['closing'], '1150.00')
        self.assertEqual(summary['net'], '150.00')
        self.assertEqual(summary['balance_difference'], '0.00')
        self.assertEqual(summary['segments_checked'], 1)

    def test_accrued_service_fee_warns_without_changing_amount(self):
        entries, summary = self.parse()
        self.assertEqual(entries[0]['amount'], Decimal('200.00'))
        self.assertEqual(summary['accrued_service_fee_control'], '-3.00')
        self.assertTrue(any('未重复加减服务费' in note for note in summary['validation_notes']))

    def test_detects_both_extensions_and_txt_prefix(self):
        for name in ('fictional.stm3', 'fictional.stmt3', 'fictional.txt'):
            with self.subTest(name=name):
                entries, summary = fmt.parse_adaptive(self.write(name=name))
                self.assertEqual(len(entries), 2)
                self.assertIn('Stmnt3', summary['format'])

    def test_crlf_records(self):
        entries, _ = fmt.fnb_stmt3_statement(self.write(newline='\r\n'))
        self.assertEqual(len(entries), 2)

    def test_rejects_truncation_and_overlong_records(self):
        for record in (self.rows[1][:-1], self.rows[1] + ' '):
            with self.subTest(length=len(record)):
                self.reject([self.rows[0], record, self.rows[2]])

    def test_rejects_blank_or_unknown_records(self):
        self.reject([self.rows[0], '', *self.rows[1:]])
        self.reject([*self.rows, replace_field(self.rows[0], 44, 44, 'Z')])

    def test_rejects_transaction_without_header(self):
        self.reject(self.rows[1:])

    def test_rejects_non_rsa_product_or_version(self):
        for start, end, text in ((1, 3, '045'), (4, 6, 'SAV'), (250, 250, '4')):
            with self.subTest(text=text):
                self.reject([replace_field(self.rows[0], start, end, text), *self.rows[1:]])

    def test_rejects_non_ascii_and_control_characters(self):
        path = self.folder / 'fictional.stmt3'
        raw = self.write().read_bytes()
        path.write_bytes(raw[:177] + b'\xe9' + raw[178:])
        with self.assertRaises(fmt.FormatError):
            fmt.fnb_stmt3_statement(path)
        self.reject([replace_field(self.rows[0], 178, 178, '\t'), *self.rows[1:]])

    def test_rejects_changed_transaction_account(self):
        self.reject([self.rows[0], replace_field(self.rows[1], 15, 30, '0000000000000456'), self.rows[2]])

    def test_rejects_changed_branch(self):
        self.reject([self.rows[0], replace_field(self.rows[1], 7, 14, '00000200'), self.rows[2]])

    def test_rejects_unknown_signs_or_non_digit_amounts(self):
        for start, end, text in ((81, 81, '?'), (68, 80, '0000000020.00'), (106, 106, '?')):
            with self.subTest(text=text):
                self.reject([self.rows[0], replace_field(self.rows[1], start, end, text), self.rows[2]])

    def test_rejects_missing_repeated_or_wrong_sequence(self):
        self.reject([self.rows[0], self.rows[2]])
        self.reject([self.rows[0], self.rows[1], self.rows[1], self.rows[2]])
        self.reject([self.rows[0], self.rows[1], replace_field(self.rows[2], 93, 98, '000003')])

    def test_rejects_header_count_or_total_mismatch(self):
        for start, end, text in ((109, 113, '00002'), (127, 131, '00002'),
                                 (114, 126, '0000000020001'), (132, 144, '0000000005001')):
            with self.subTest(start=start):
                self.reject([replace_field(self.rows[0], start, end, text), *self.rows[1:]])

    def test_rejects_header_balance_mismatch(self):
        self.reject([header(closing=115001), *self.rows[1:]])

    def test_rejects_invalid_or_reversed_header_dates(self):
        self.reject([header(statement_date='20260230'), *self.rows[1:]])
        self.reject([header(prior='20261001'), *self.rows[1:]])

    def test_rejects_invalid_transaction_date(self):
        self.reject([self.rows[0], replace_field(self.rows[1], 45, 52, '20260931'), self.rows[2]])

    def test_negative_opening_and_closing(self):
        rows = [header(opening=-100000, closing=-85000), *self.rows[1:]]
        _, summary = self.parse(rows)
        self.assertEqual(summary['opening'], '-1000.00')
        self.assertEqual(summary['closing'], '-850.00')
        self.assertEqual(summary['balance_difference'], '0.00')

    def test_multiple_contiguous_segments(self):
        rows = self.rows + [header(opening=115000, closing=116000, credits=1, credit_total=1000,
                                  debits=0, debit_total=0, statement='00002', prior='20260930'),
                            transaction(1, 1000, '20260930')]
        entries, summary = self.parse(rows)
        self.assertEqual(len(entries), 3)
        self.assertEqual(summary['segments_checked'], 2)
        self.assertEqual(summary['net'], '160.00')
        self.assertEqual(summary['closing'], '1160.00')

    def test_rejects_non_contiguous_segments(self):
        rows = self.rows + [header(opening=115001, closing=116001, credits=1, credit_total=1000,
                                  debits=0, debit_total=0, statement='00002'), transaction(1, 1000)]
        self.reject(rows)

    def test_rejects_multi_account_and_currency_segments(self):
        second = header(opening=115000, closing=115000, credits=0, credit_total=0, debits=0, debit_total=0)
        for start, end, text in ((15, 30, '0000000000000456'), (241, 243, 'USD')):
            with self.subTest(text=text):
                self.reject(self.rows + [replace_field(second, start, end, text)])

    def test_preserves_paper_description_and_legacy_narrative(self):
        rows = [self.rows[0], transaction(1, 20000, description='PAPER DETAIL', narrative='LEGACY DETAIL', reference=''), self.rows[2]]
        entries, _ = self.parse(rows)
        self.assertEqual(entries[0]['description'], 'PAPER DETAIL / LEGACY DETAIL')

    def test_cheque_fallback_does_not_replace_paper_reference(self):
        rows = [self.rows[0], transaction(1, 20000, reference='', cheque=123), self.rows[2]]
        entries, _ = self.parse(rows)
        self.assertEqual(entries[0]['reference'], '000000000123')
        rows[1] = transaction(1, 20000, reference='PAPER REFERENCE', cheque=123)
        entries, summary = self.parse(rows)
        self.assertEqual(entries[0]['reference'], 'PAPER REFERENCE')
        self.assertTrue(any('兼容支票号' in note for note in summary['validation_notes']))

    def test_effective_date_aliases_do_not_apply_fee_indicator_to_amount(self):
        for date_header in ('Effective Date', 'Effective Value Date'):
            with self.subTest(header=date_header):
                path = self.folder / 'fictional.csv'
                with path.open('w', encoding='utf-8', newline='') as handle:
                    csv.writer(handle).writerows([
                        [date_header, 'Description', 'Reference', 'Service Fee', 'Debit / Credit Indicator', 'Amount', 'Balance'],
                        ['20260901', 'RENT', 'FICTIONAL REF', '3.00', 'D', '200.00', '1200.00'],
                        ['20260902', 'PAYMENT', 'FICTIONAL REF', '0.00', 'C', '-50.00', '1150.00'],
                    ])
                entries, summary = fmt.generic_statement(path)
                self.assertEqual([e['amount'] for e in entries], [Decimal('200.00'), Decimal('-50.00')])
                self.assertEqual(summary['net'], '150.00')
                self.assertTrue(any('Service Fee' in note for note in summary['validation_notes']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
