"""Synthetic regression cases for conservative QIF and OFC adapters."""
from pathlib import Path
from tempfile import TemporaryDirectory
from decimal import Decimal
import os
import sys
import unittest
import csv
import json
import subprocess

ROOT=Path(os.environ.get('BANK_TOOL_ROOT',Path(__file__).resolve().parents[1])).resolve()
sys.path.insert(0,str(ROOT))
import bank_formats as formats
import bank_merge as bank


QIF = '!Type:Bank\nD09/13/2026\nT200.00\nN1\nMSYNTHETIC RECEIPT\n^\nD09/14/2026\nT-50.00\nN2\nMSYNTHETIC PAYMENT\n^\n'
OFC = '''<OFC><DTD>1<CPAGE>1252<ACCTSTMT>
<ACCTFROM><BANKID>12345600<ACCTID>000789<ACCTTYPE>0</ACCTFROM>
<STMTRS><DTSTART>20260901<DTEND>20260930<LEDGER>1150.00
<STMTTRN><TRNTYPE>0<DTPOSTED>20260913<TRNAMT>200.00<FITID>SYNTHETIC-1<MEMO>SYNTHETIC RECEIPT</STMTTRN>
<STMTTRN><TRNTYPE>0<DTPOSTED>20260914115959<TRNAMT>-50.00<FITID>SYNTHETIC-2<MEMO>SYNTHETIC PAYMENT</STMTTRN>
</STMTRS></ACCTSTMT></OFC>'''


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def file(self,text,suffix,encoding='utf-8'):
        p=self.root/('synthetic'+suffix);p.write_text(text,encoding=encoding);return p
    def qif(self,text=QIF,order='AUTO'):
        return formats.qif_statement(self.file(text,'.qif'),order)
    def ofc(self,text=OFC):return formats.ofc_statement(self.file(text,'.ofc'))
    def test_qif_unambiguous_auto_and_signed_values(self):
        entries,summary=self.qif()
        self.assertEqual([e['amount'] for e in entries],[Decimal('200.00'),Decimal('-50.00')])
        self.assertEqual([e['date'].strftime('%Y-%m-%d') for e in entries],['2026-09-13','2026-09-14'])
        self.assertEqual([e['reference'] for e in entries],['1','2'])
        self.assertEqual(summary['net'],'150.00')
        self.assertIsNone(summary['opening']);self.assertIsNone(summary['closing'])
        self.assertIn('未核平',summary['validation'])
    def test_qif_default_rejects_ambiguous_date(self):
        with self.assertRaisesRegex(formats.FormatError,'不明确'):self.qif(QIF.replace('09/13/2026','09/03/2026'))
    def test_qif_general_dmy_cannot_silently_override_qif_auto(self):
        path=self.file(QIF.replace('09/13/2026','09/03/2026'),'.qif')
        with self.assertRaisesRegex(formats.FormatError,'不明确'):formats.parse_adaptive(path,date_order='DMY')
    def test_qif_explicit_mdy(self):
        entries,_=self.qif(QIF.replace('09/13/2026','09/03/2026'),'MDY')
        self.assertEqual(entries[0]['date'].strftime('%Y-%m-%d'),'2026-09-03')
    def test_qif_explicit_dmy(self):
        entries,_=self.qif(QIF.replace('09/13/2026','03/09/2026').replace('09/14/2026','04/09/2026'),'DMY')
        self.assertEqual(entries[0]['date'].strftime('%Y-%m-%d'),'2026-09-03')
    def test_qif_explicit_routing_parameter(self):
        path=self.file(QIF.replace('09/13/2026','09/03/2026'),'.qif')
        entries,_=formats.parse_adaptive(path,qif_date_order='MDY')
        self.assertEqual(entries[0]['date'].strftime('%Y-%m-%d'),'2026-09-03')
    def test_qif_public_prepare_requires_source_specific_date_choice(self):
        path=self.file(QIF.replace('09/13/2026','09/03/2026'),'.qif')
        checks,_=bank.inspect_sources(files=[path],profiles=[])
        self.assertFalse(checks[0]['ok'])
        tx,summary=bank.prepare(files=[path],profiles=[],qif_date_order='MDY')
        self.assertEqual(summary['period'],'2026-09')
        self.assertEqual(tx[0].date.strftime('%Y-%m-%d'),'2026-09-03')
        destination=bank.write_result(tx,summary,self.root/'outputs')
        with (destination/'ERP_import_2026-09.csv').open(encoding='utf-8-sig',newline='') as handle:
            rows=list(csv.reader(handle))
        self.assertEqual(rows[1],['01/09/2026']+['']*6)
        self.assertEqual(rows[-1],['30/09/2026']+['']*6)
        self.assertEqual(len(rows),5)
    def test_qif_cli_explicit_date_choice_reaches_preflight(self):
        path=self.file(QIF.replace('09/13/2026','09/03/2026'),'.qif')
        result=subprocess.run([sys.executable,'-X','utf8','-B',str(ROOT/'bank_merge.py'),'--files',str(path),'--qif-date-order','MDY','--check'],capture_output=True,text=True,encoding='utf-8',timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        check=json.loads(result.stdout)['checks'][0]
        self.assertTrue(check['ok'],check)
        self.assertEqual(check['months'],['2026-09'])
    def test_qif_apostrophe_year_and_whitespace(self):
        entries,_=self.qif(QIF.replace('09/13/2026',"09/ 13'26"))
        self.assertEqual(entries[0]['date'].year,2026)
    def test_qif_plain_two_digit_year_is_not_guessed(self):
        with self.assertRaisesRegex(formats.FormatError,'四位年份'):self.qif(QIF.replace('09/13/2026','09/13/26'))
    def test_qif_account_name_and_u_amount(self):
        entries,summary=self.qif('!Account\nNSYNTHETIC BANK ACCOUNT\nTBank\n^\n'+QIF.replace('T200.00','T200.00\nU200.00'))
        self.assertEqual(summary['account'],'SYNTHETIC BANK ACCOUNT');self.assertEqual(len(entries),2)
    def test_qif_u_only_amount(self):
        entries,_=self.qif(QIF.replace('T200.00','U200.00'))
        self.assertEqual(entries[0]['amount'],Decimal('200'))
    def test_qif_t_u_disagreement_rejected(self):
        with self.assertRaisesRegex(formats.FormatError,'不一致'):self.qif(QIF.replace('T200.00','T200.00\nU201.00'))
    def test_qif_duplicate_record_retained(self):
        entries,_=self.qif(QIF+QIF.split('\n',1)[1])
        self.assertEqual(len(entries),4)
    def test_qif_zero_amount_retained(self):
        entries,_=self.qif(QIF.replace('T200.00','T0.00'))
        self.assertEqual(entries[0]['amount'],Decimal('0.00'));self.assertEqual(len(entries),2)
    def test_qif_multisection_rejected(self):
        with self.assertRaisesRegex(formats.FormatError,'多个 Bank'):self.qif(QIF+QIF)
    def test_qif_multiaccount_list_rejected(self):
        with self.assertRaisesRegex(formats.FormatError,'多个账户'):self.qif('!Account\nNfirst\nTBank\n^\nNsecond\nTBank\n^\n'+QIF)
    def test_qif_unsupported_investment_rejected(self):
        with self.assertRaisesRegex(formats.FormatError,'仅支持'):self.qif(QIF.replace('!Type:Bank','!Type:Invst'))
    def test_qif_split_records_rejected_without_flattening(self):
        with self.assertRaisesRegex(formats.FormatError,'拆分'):self.qif(QIF.replace('N1','N1\nSCategory\n$200.00'))
    def test_qif_truncation_rejected(self):
        with self.assertRaisesRegex(formats.FormatError,'结束符'):self.qif(QIF.rstrip()[:-1])
    def test_qif_repeated_amount_rejected(self):
        with self.assertRaisesRegex(formats.FormatError,'重复'):self.qif(QIF.replace('T200.00','T200.00\nT200.00'))
    def test_qif_unknown_field_rejected(self):
        with self.assertRaisesRegex(formats.FormatError,'不支持字段'):self.qif(QIF.replace('N1','N1\nZunknown'))
    def test_qif_opening_setup_not_imported_as_transaction(self):
        with self.assertRaisesRegex(formats.FormatError,'余额记录'):self.qif(QIF.replace('MSYNTHETIC RECEIPT','POpening Balance'))
    def test_qif_non_cent_amount_rejected(self):
        with self.assertRaises(formats.FormatError):self.qif(QIF.replace('T200.00','T200.001'))
    def test_qif_header_content_sniffing(self):
        entries,_=formats.parse_adaptive(self.file(QIF,'.txt'))
        self.assertEqual(len(entries),2)
    def test_ofc_fnb_sign_comes_from_amount_not_trntype(self):
        entries,summary=self.ofc()
        self.assertEqual(entries[1]['amount'],Decimal('-50.00'))
        self.assertEqual(summary['closing'],'1150.00');self.assertIsNone(summary['opening'])
        self.assertIsNone(summary['balance_difference']);self.assertIn('未核平',summary['validation'])
        self.assertEqual(summary['account'],'000789')
    def test_ofc_explicit_leaf_closing_tags(self):
        entries,_=self.ofc(OFC.replace('<TRNAMT>200.00','<TRNAMT>200.00</TRNAMT>'))
        self.assertEqual(entries[0]['amount'],Decimal('200.00'))
    def test_ofc_decimal_comma(self):
        entries,_=self.ofc(OFC.replace('200.00','200,00'))
        self.assertEqual(entries[0]['amount'],Decimal('200.00'))
    def test_ofc_missing_ledger_keeps_warning(self):
        _,summary=self.ofc(OFC.replace('<LEDGER>1150.00',''))
        self.assertIsNone(summary['closing']);self.assertIn('未核平',summary['validation'])
    def test_ofc_does_not_deduplicate_fitid(self):
        entries,_=self.ofc(OFC.replace('SYNTHETIC-2','SYNTHETIC-1'))
        self.assertEqual(len(entries),2)
    def test_ofc_acctto_not_confused_with_source(self):
        text=OFC.replace('<MEMO>SYNTHETIC PAYMENT','<ACCTTO><BANKID>111111<ACCTID>000999<ACCTTYPE>0</ACCTTO><MEMO>SYNTHETIC PAYMENT')
        entries,summary=self.ofc(text)
        self.assertEqual(entries[1]['account'],'000789');self.assertEqual(summary['account'],'000789')
    def test_ofc_payee_name(self):
        text=OFC.replace('<MEMO>SYNTHETIC RECEIPT','<PAYEE><NAME>SYNTHETIC PAYEE<ADDRESS>Line one<ADDRESS>Line two</PAYEE><MEMO>SYNTHETIC RECEIPT')
        entries,_=self.ofc(text)
        self.assertEqual(entries[0]['description'],'SYNTHETIC PAYEE / SYNTHETIC RECEIPT')
    def test_ofc_name_and_payee_conflict(self):
        with self.assertRaisesRegex(formats.FormatError,'同时包含'):
            self.ofc(OFC.replace('<MEMO>SYNTHETIC RECEIPT','<NAME>ONE<PAYEE><NAME>TWO</PAYEE><MEMO>SYNTHETIC RECEIPT'))
    def test_ofc_cpage_decodes_accent_without_guessing(self):
        path=self.file(OFC.replace('SYNTHETIC RECEIPT','SYNTHETIC CAFÉ'),'.ofc',encoding='cp1252')
        entries,_=formats.ofc_statement(path)
        self.assertEqual(entries[0]['description'],'SYNTHETIC CAFÉ')
    def test_ofc_multiple_source_accounts_rejected(self):
        with self.assertRaises(formats.FormatError):self.ofc(OFC.replace('</ACCTFROM>','</ACCTFROM><ACCTFROM><ACCTID>000999</ACCTFROM>'))
    def test_ofc_multiple_statements_rejected(self):
        with self.assertRaises(formats.FormatError):self.ofc(OFC.replace('</OFC>',OFC[OFC.index('<ACCTSTMT>'):OFC.index('</OFC>')]+'</OFC>'))
    def test_ofc_missing_transaction_close_rejected(self):
        with self.assertRaises(formats.FormatError):self.ofc(OFC.replace('</STMTTRN>','',1))
    def test_ofc_root_truncation_rejected(self):
        with self.assertRaisesRegex(formats.FormatError,'结束标签'):self.ofc(OFC.replace('</OFC>',''))
    def test_ofc_unknown_financial_block_rejected(self):
        with self.assertRaisesRegex(formats.FormatError,'未支持标签'):self.ofc(OFC.replace('<LEDGER>1150.00','<AVAILABLE>1150.00'))
    def test_ofc_duplicate_amount_rejected(self):
        with self.assertRaisesRegex(formats.FormatError,'重复'):self.ofc(OFC.replace('<TRNAMT>200.00','<TRNAMT>200.00<TRNAMT>200.00'))
    def test_ofc_missing_amount_rejected(self):
        with self.assertRaises(formats.FormatError):self.ofc(OFC.replace('<TRNAMT>200.00',''))
    def test_ofc_non_cent_amount_rejected(self):
        with self.assertRaises(formats.FormatError):self.ofc(OFC.replace('200.00','200.001'))
    def test_ofc_invalid_date_rejected(self):
        with self.assertRaises(ValueError):self.ofc(OFC.replace('20260913','20260231'))
    def test_ofc_invalid_time_rejected(self):
        with self.assertRaises(ValueError):self.ofc(OFC.replace('20260914115959','20260914255959'))
    def test_ofc_header_content_sniffing(self):
        entries,_=formats.parse_adaptive(self.file(OFC,'.txt'))
        self.assertEqual(len(entries),2)


if __name__=='__main__':unittest.main(verbosity=2)
