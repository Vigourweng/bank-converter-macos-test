"""Portable parser regressions. Every statement and account in this file is fictional.

Run as python3 tests/test_formats.py from the repository. BANK_TOOL_ROOT may
point at an extracted application folder for release testing. No network or
third-party test dependencies are used. A compressed fictional BIFF8 fixture checks the bundled XLS reader.
"""
import sys
import os
import hashlib
sys.dont_write_bytecode=True
import csv
import base64
import zlib
import io
import json
import tempfile
import unittest
import zipfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape

ROOT=Path(os.environ.get('BANK_TOOL_ROOT', Path(__file__).resolve().parents[1])).resolve()
sys.path.insert(0,str(ROOT))
import bank_formats as fmt
import bank_merge as bank

BASE=[['Date','Description','Amount','Balance','Account Number'],
      ['','Opening balance','','1000','000123'],
      ['01/09/2026','RENT','200','1200','000123'],
      ['02/09/2026','PAYMENT','-50','1150','000123'],
      ['','Closing balance','','1150','000123']]

def xlsx_fixture(path, sheets, date1904=False):
    with zipfile.ZipFile(path,'w') as z:
        names=''.join(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>' for i,(name,rows) in enumerate(sheets,1))
        z.writestr('xl/workbook.xml',f'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><workbookPr date1904="{int(date1904)}"/><sheets>{names}</sheets></workbook>')
        rels=''.join(f'<Relationship Id="rId{i}" Target="worksheets/sheet{i}.xml" Type="worksheet"/>' for i in range(1,len(sheets)+1))
        z.writestr('xl/_rels/workbook.xml.rels',f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>')
        z.writestr('xl/styles.xml','<styleSheet><cellXfs><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>')
        for i,(name,rows) in enumerate(sheets,1):
            data=[]
            for n,row in enumerate(rows,1):
                cells=[]
                for j,value in enumerate(row):
                    coord=chr(65+j)+str(n)
                    if isinstance(value,tuple):cells.append(f'<c r="{coord}" s="1"><v>{value[0]}</v></c>')
                    else:cells.append(f'<c r="{coord}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
                data.append(f'<row r="{n}">{"".join(cells)}</row>')
            z.writestr(f'xl/worksheets/sheet{i}.xml','<worksheet><sheetData>'+''.join(data)+'</sheetData></worksheet>')

OFX='''OFXHEADER:100
DATA:OFXSGML
<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>ZAR
<BANKACCTFROM><BANKID>1<ACCTID>000321<ACCTTYPE>CHECKING</BANKACCTFROM>
<BANKTRANLIST><DTSTART>20260901<DTEND>20260930
<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20260901120000[2:SAST]<TRNAMT>200.00<FITID>A<NAME>Rent<MEMO>Shop 1</STMTTRN>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260902<TRNAMT>-50.00<FITID>B<NAME>Fee</STMTTRN>
</BANKTRANLIST><LEDGERBAL><BALAMT>1150.00<DTASOF>20260930</LEDGERBAL></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>'''

CAMT='''<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02"><BkToCstmrStmt><Stmt>
<Acct><Id><Othr><Id>000222</Id></Othr></Id></Acct>
<Bal><Tp><CdOrPrtry><Cd>OPBD</Cd></CdOrPrtry></Tp><Amt Ccy="ZAR">1000.00</Amt><CdtDbtInd>CRDT</CdtDbtInd></Bal>
<Bal><Tp><CdOrPrtry><Cd>CLBD</Cd></CdOrPrtry></Tp><Amt Ccy="ZAR">1150.00</Amt><CdtDbtInd>CRDT</CdtDbtInd></Bal>
<Ntry><Amt Ccy="ZAR">200.00</Amt><CdtDbtInd>CRDT</CdtDbtInd><Sts><Cd>BOOK</Cd></Sts><BookgDt><Dt>2026-09-01</Dt></BookgDt><AcctSvcrRef>AA</AcctSvcrRef><AddtlNtryInf>RENT</AddtlNtryInf></Ntry>
<Ntry><Amt Ccy="ZAR">50.00</Amt><CdtDbtInd>DBIT</CdtDbtInd><Sts>BOOK</Sts><BookgDt><Dt>2026-09-02</Dt></BookgDt><NtryRef>BB</NtryRef><AddtlNtryInf>PAYMENT</AddtlNtryInf></Ntry>
</Stmt></BkToCstmrStmt></Document>'''

MT='''{1:F01TEST}{4:
:20:TEST
:25:000456
:28C:1/1
:60F:C260901ZAR1000,00
:61:2609010901C200,00NTRFREF1//BANK1
:86:RENT
:61:2609020902D50,00NCHGREF2//BANK2
:86:FEE
:62F:C260930ZAR1150,00
-}'''

# Fixed BIFF8 workbook generated with xlwt from BASE only; no user files.
SYNTHETIC_XLS_ZLIB_BASE64 = (
    'eNrtWE1IVFEUPve9O86POj+mgQYyCP2Z4cyEUQudtMyVk1nQD0HNjI8a1DcyjgtblGUug6CV0UawRRvLTT/UonYt'
    'AqMWQRBoLYMgKGihvs49777XjBk4UIbxvuGde8+55373vnPuve/NezUXmp+arVuAFWgDFZYNL5QV2BheXksJArYb'
    'hqhapQcvw8GGgteDiSxzwZPKl26RQ5HvBVDgPn+OEuADXmdgCBJZXQuvIzpoDkkm5tCKksFttPihlmZVRTJNchPJ'
    'e+T5lOQBslwn2Yq+8+w0zMUTjfvkKj6pNFCbHwTvQ+rzjixRqIEXYhVfvsFMXxe05zLJgQ3WUM8rYBowoV2aruWE'
    'Xg53RG4jhHmoRmUavhlhgK/Wjn4Wduzra2eA9u/Fdvdv7B5M4Er7TYUDjIFxjjbIBIRgkgMd0sdG9fwFLZ9JL8Is'
    'mum4Bmw7lMxr5Vhow+lcZiifyeq4KNoHsyN6HldLR3Igqac1P5rSaWELJ0YGU1ouAHBkSNMz+vlwynRBqiguJB+u'
    'qGhzZH9zLBLbi7bezsRxFSAWiQiHmOkQsxxwhJ72U92mz+4W8om2RJD94EB2uIDdJ+6BDqZg0cFUSRu2AmUfBKge'
    'om0bxOgs3v3yujvVEz9LljF6GJmPrK0iSmDAFdEDO/upxWXLRuqxi+RVYt1C9TqS1eiD5baeGlk5PE4+16h1G46z'
    'h/Amvr2gvgPrE5+OPqqf+BjfifWZroWL1TNv41PQgNnpw/7iNw5NrIndmhR4HLdKJo+39yRrfznqPEpQzt2Qz+UA'
    'LIGPqiGSpiaiw2xNkbEyNRU11dY4alwysxXMTCkjW4h8GbGKqJl2xbYLRpGjS4rQON7DZ2YyKqvMVSFGt5yBmUmP'
    'rQlWr60JZp9kVoqY1VWYVWIul71VYq6wNcFcaWuC2S+Z1SJmvkoUuB0FTqwBGQVuR4EXMXKbsU2pggfilvHR9BM+'
    'cODAgQMHDhw4+Adg9GoI9NrE5QuxeI1zy+86S3gtO59J/lv0QhZ/efxj2Qk6ljkYLWn9bAYXs7jYGvtY3wsFTuDo'
    'OeiHFM2jv+T1i3+VWOH9rLlj8M9toVLHXy5lnn95/B9K5Ake'
)

class AdaptiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)
    def write(self,rows=BASE,name='bank.csv',delimiter=',',encoding='utf-8-sig'):
        path=self.folder/name
        with path.open('w',encoding=encoding,newline='') as h:csv.writer(h,delimiter=delimiter).writerows(rows)
        return path
    def text(self,name,text):
        path=self.folder/name;path.write_text(text,encoding='utf-8');return path
    def parse(self,path,**kw):return bank.parse_source(path,**kw)

    def test_amount_notations(self):
        pairs={'1,234.56':'1234.56','1.234,56':'1234.56','1 234,56':'1234.56','(100.00)':'-100.00',
               '100.00 DR':'-100.00','100 CR':'100.00','R 1,000.00':'1000.00','-R 1,000.00':'-1000.00','100-':'-100.00',
               'ZAR 100.00':'100.00','1\u00a0234.56':'1234.56','1\'234.56':'1234.56'}
        for s,want in pairs.items():
            with self.subTest(s=s):self.assertEqual(fmt.parse_money(s),Decimal(want))
    def test_invalid_amounts(self):
        for s in ('NaN','1.001','1,23,45','-100 CR','(-100)','one hundred'):
            with self.subTest(s=s),self.assertRaises(fmt.FormatError):fmt.parse_money(s)
    def test_date_notations(self):
        for s in ('20260901','020260901','2026-09-01','2026/09/01','01/09/2026','1.9.2026','01 Sep 2026','September 1, 2026','2026-09-01T12:30:00'):
            with self.subTest(s=s):self.assertEqual(fmt.parse_date(s),datetime(2026,9,1))
    def test_date_order(self):
        self.assertEqual(fmt.parse_date('09/01/2026','MDY'),datetime(2026,9,1))
        with self.assertRaises(fmt.FormatError):fmt.parse_date('01/09/2026','AUTO')
        self.assertEqual(fmt.parse_date('23/09/2026','AUTO'),datetime(2026,9,23))
        self.assertEqual(fmt.parse_date('09/23/2026','AUTO'),datetime(2026,9,23))
    def test_generic_balance_and_accounts(self):
        tx,s=self.parse(self.write());self.assertEqual(len(tx),2);self.assertEqual(s['balance_difference'],'0.00')
        self.assertEqual(s['account'],'000123');self.assertEqual(s['net'],'150.00')
    def test_delimiters_and_encodings(self):
        for delimiter,extension,encoding in [(';','.csv','utf-8'),('\t','.tsv','utf-16'),('|','.txt','cp1252'),(',','.csv','utf-8-sig')]:
            with self.subTest(delimiter=delimiter):
                tx,s=self.parse(self.write(name='a'+extension,delimiter=delimiter,encoding=encoding));self.assertEqual(len(tx),2)
    def test_sep_directive(self):
        p=self.write(delimiter=';');p.write_text('sep=;\n'+p.read_text(encoding='utf-8-sig'),encoding='utf-8')
        tx,s=self.parse(p);self.assertEqual(len(tx),2);self.assertEqual(tx[0].source_row,4)
    def test_metadata_before_header(self):
        tx,s=self.parse(self.write([['Bank statement'],['Export 2026-09'],[]]+BASE));self.assertEqual(len(tx),2);self.assertEqual(s['header_row'],4)
    def test_chinese_debit_credit(self):
        rows=[['交易日期','摘要','收入','支出','交易后余额','账号'],['2026/09/01','租金','200','','1200','0001'],['2026/09/02','付款','','50','1150','0001']]
        tx,s=self.parse(self.write(rows));self.assertEqual([t.amount for t in tx],[Decimal('200'),Decimal('-50')]);self.assertIsNone(s['balance_difference'])
    def test_direction_column(self):
        tx,s=self.parse(self.write([['Date','Description','Amount','DR/CR'],['01/09/2026','PAY','100','DR'],['02/09/2026','RENT','200','CR']]))
        self.assertEqual([t.amount for t in tx],[Decimal('-100'),Decimal('200')])
    def test_both_debit_and_credit_block(self):
        with self.assertRaises(bank.InputError):self.parse(self.write([['Date','Debit','Credit'],['01/09/2026','10','20']]))
    def test_negative_credit_conflict(self):
        with self.assertRaises(bank.InputError):self.parse(self.write([['Date','Amount','DR/CR'],['01/09/2026','-100','CR']]))
    def test_unsigned_amount_uses_debit_column(self):
        tx,s=self.parse(self.write([['Date','Amount','Debit','Credit'],['01/09/2026','100','100','']]))
        self.assertEqual(tx[0].amount,Decimal('-100'))
    def test_amount_columns_mismatch_blocks(self):
        with self.assertRaises(bank.InputError):self.parse(self.write([['Date','Amount','Debit'],['01/09/2026','50','100']]))
    def test_balance_mismatch(self):
        rows=[r.copy() for r in BASE];rows[3][3]='1149'
        with self.assertRaises(bank.InputError):self.parse(self.write(rows))
    def test_no_balances_not_claimed_pass(self):
        tx,s=self.parse(self.write([['Date','Description','Amount'],['01/09/2026','X','10']]))
        self.assertIsNone(s['balance_difference']);self.assertIn('未核平',s['validation'])
        _,report=bank.prepare(files=[self.folder/'bank.csv']);self.assertTrue(report['warnings'])
    def test_reverse_balance_chain(self):
        rows=[BASE[0],BASE[3],BASE[2]]
        tx,s=self.parse(self.write(rows));self.assertIn('余额链核对通过',s['validation'])
    def test_total_row_not_transaction(self):
        tx,s=self.parse(self.write(BASE+[['Total','','150','','']]));self.assertEqual(len(tx),2)
    def test_unknown_footer_not_silently_skipped(self):
        with self.assertRaises(bank.InputError):self.parse(self.write(BASE+[['','Something else','999','','']]))
    def test_repeated_headers(self):
        tx,s=self.parse(self.write(BASE[:3]+[BASE[0]]+BASE[3:]));self.assertEqual(len(tx),2)
    def test_currency_mix(self):
        rows=[['Date','Amount','Currency'],['01/09/2026','10','ZAR'],['02/09/2026','20','USD']]
        with self.assertRaises(bank.InputError):self.parse(self.write(rows))
    def test_multiple_source_accounts_block(self):
        rows=[['Date','Amount','Account'],['01/09/2026','10','1'],['02/09/2026','20','2']]
        with self.assertRaises(bank.InputError):self.parse(self.write(rows))
    def test_cross_file_currency_mix(self):
        a=self.write([['Date','Amount','Currency','Account'],['01/09/2026','10','ZAR','1']],name='a.csv')
        b=self.write([['Date','Amount','Currency','Account'],['01/09/2026','10','USD','2']],name='b.csv')
        with self.assertRaises(bank.InputError):bank.prepare(files=[a,b])
    def test_duplicate_content_renamed(self):
        a=self.write([['Date','Amount'],['01/09/2026','10']],name='a.csv');b=self.folder/'b.csv';b.write_bytes(a.read_bytes())
        with self.assertRaises(bank.InputError):bank.prepare(files=[a,b])
    def test_profiles_custom_headers(self):
        p=self.write([['WHEN','WHAT','VALUE'],['01/09/2026','RENT','200']])
        profile={'signature':fmt.signature(['WHEN','WHAT','VALUE']),'columns':{'date':[0],'description':[1],'amount':[2]},'date_order':'DMY'}
        tx,s=self.parse(p,profiles=[profile]);self.assertEqual(tx[0].amount,200);self.assertIn('自定义',s['format'])
    def test_profile_reused_another_month(self):
        p=self.write([['WHEN','VALUE'],['01/10/2026','200']])
        profile={'signature':fmt.signature(['WHEN','VALUE']),'columns':{'date':[0],'amount':[1]},'date_order':'DMY'}
        tx,s=self.parse(p,profiles=[profile]);self.assertEqual(tx[0].date.month,10)
    def test_priority_booking_date(self):
        p=self.write([['Value Date','Amount','Booking Date'],['02/09/2026','20','01/09/2026']])
        tx,s=self.parse(p);self.assertEqual(tx[0].date.day,1)
    def test_multiple_description_columns(self):
        p=self.write([['Date','Amount','Description 1','Description 2'],['01/09/2026','20','A','B']])
        tx,s=self.parse(p);self.assertEqual(tx[0].description,'A / B')
    def test_preflight_collects_all_errors(self):
        a=self.write(name='good.csv');b=self.text('bad.csv','Unknown,Other\na,b\n')
        checks,_=bank.inspect_sources(files=[a,b]);self.assertEqual(sum(c['ok'] for c in checks),1);self.assertEqual(len(checks),2)
    def test_generic_output_month_boundaries(self):
        p=self.write();tx,report=bank.prepare(files=[p]);out=bank.write_result(tx,report,self.folder/'out')
        with (out/'ERP_import_2026-09.csv').open(encoding='utf-8') as handle: rows=list(csv.reader(handle))
        self.assertEqual(rows[1],['01/09/2026']+['']*6);self.assertEqual(rows[-1],['30/09/2026']+['']*6);self.assertEqual(len(rows),5)
    def test_xlsx_inline(self):
        p=self.folder/'bank.xlsx';xlsx_fixture(p,[('Statement',BASE)])
        tx,s=self.parse(p);self.assertEqual(len(tx),2);self.assertEqual(s['balance_difference'],'0.00')
    def test_same_day_reverse_balances(self):
        rows=[['Date','Amount','Balance'],['01/09/2026','-50','1150'],['01/09/2026','200','1200']]
        tx,s=self.parse(self.write(rows));self.assertIn('余额链核对通过',s['validation'])
    def test_balance_chain_constant_offset_is_rejected(self):
        rows=[r.copy() for r in BASE];rows[2][3]='2200';rows[3][3]='2150'
        with self.assertRaises(bank.InputError):self.parse(self.write(rows))
    def test_xlsx_serial_dates(self):
        rows=[r.copy() for r in BASE]
        for i in (2,3):rows[i][0]=((datetime(2026,9,i-1)-datetime(1899,12,30)).days,)
        p=self.folder/'bank.xlsx';xlsx_fixture(p,[('Statement',rows)])
        tx,s=self.parse(p);self.assertEqual(tx[0].date,datetime(2026,9,1))
    def test_xlsx_1904_dates(self):
        rows=[['Date','Amount'],[((datetime(2026,9,1)-datetime(1904,1,1)).days,),'20']]
        p=self.folder/'bank.xlsx';xlsx_fixture(p,[('Statement',rows)],True)
        tx,s=self.parse(p);self.assertEqual(tx[0].date,datetime(2026,9,1))
    def test_multiple_worksheets_requires_selection(self):
        p=self.folder/'bank.xlsx';xlsx_fixture(p,[('A',BASE),('B',BASE)])
        with self.assertRaises(bank.InputError):self.parse(p)
        profile={'signature':fmt.signature(BASE[0]),'columns':{'date':[0],'description':[1],'amount':[2],'balance':[3],'account':[4]},'sheet':'B'}
        tx,s=self.parse(p,profiles=[profile]);self.assertEqual(s['sheet'],'B');self.assertEqual(len(tx),2)
    def test_html_xls(self):
        text='<html><table>'+''.join('<tr>'+''.join('<td>'+escape(c)+'</td>' for c in row)+'</tr>' for row in BASE)+'</table></html>'
        tx,s=self.parse(self.text('bank.xls',text));self.assertEqual(len(tx),2)
    def test_text_disguised_xls(self):
        tx,s=self.parse(self.write(name='bank.xls',delimiter='\t'));self.assertEqual(len(tx),2)
    def test_ofx_sgml(self):
        tx,s=self.parse(self.text('bank.ofx',OFX));self.assertEqual(len(tx),2);self.assertEqual(tx[1].amount,-50);self.assertEqual(s['account'],'000321');self.assertIsNone(s['balance_difference'])
    def test_ofx_multiple_accounts_block(self):
        with self.assertRaises(bank.InputError):self.parse(self.text('bank.ofx',OFX.replace('</STMTTRNRS>','</STMTTRNRS><STMTTRNRS><STMTRS></STMTRS></STMTTRNRS>')))
    def test_camt(self):
        tx,s=self.parse(self.text('bank.xml',CAMT));self.assertEqual([t.amount for t in tx],[Decimal('200'),Decimal('-50')]);self.assertEqual(s['balance_difference'],'0.00')
    def test_camt_pending_blocks(self):
        with self.assertRaises(bank.InputError):self.parse(self.text('bank.xml',CAMT.replace('BOOK','PDNG')))
    def test_camt_wrong_balance(self):
        with self.assertRaises(bank.InputError):self.parse(self.text('bank.xml',CAMT.replace('1150.00','1150.01')))
    def test_mt940(self):
        tx,s=self.parse(self.text('bank.mt940',MT));self.assertEqual(len(tx),2);self.assertEqual(s['balance_difference'],'0.00');self.assertEqual(tx[1].description,'FEE')
    def test_mt940_reversal(self):
        text=MT.replace('D50,00','RD50,00').replace('ZAR1150,00','ZAR1250,00')
        tx,s=self.parse(self.text('bank.sta',text));self.assertEqual(tx[1].amount,50);self.assertEqual(s['net'],'250.00')
    def test_mt940_bad_balance(self):
        with self.assertRaises(bank.InputError):self.parse(self.text('bank.mt940',MT.replace('ZAR1150,00','ZAR1151,00')))
    def test_mt940_txt_detection(self):
        tx,s=self.parse(self.text('bank.txt',MT));self.assertEqual(len(tx),2)

    def test_legacy_no_header_xlsx_synthetic(self):
        path=self.folder/'legacy.xlsx'
        rows=[['0','000','BRANCH','0','','SYNTHETIC','0','0'],
              ['','000456','ACC-NO','0','','','0','0'],
              ['','0','OPEN','1000','OPEN BALANCE','','0','0'],
              ['HIST','20260901','','200','RENT','','0','0'],
              ['HIST','20260902','','-50','FEE','','0','0'],
              ['','0','CLOSE','1150','CLOSE BALANCE','','0','0']]
        xlsx_fixture(path,[('Synthetic statement',rows)])
        tx,summary=self.parse(path)
        self.assertEqual(len(tx),2)
        self.assertEqual(summary['balance_difference'],'0.00')

    def test_two_sources_preserve_inputs_and_boundary_rows(self):
        first=self.write(name='Account A.csv')
        second=self.write([['Date','Description','Amount','Account'],['30/09/2026','INTEREST','10','000456']],name='Account B.csv')
        before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in (first,second)}
        tx,report=bank.prepare(files=[first,second],profiles=[])
        self.assertEqual(len(tx),3)
        self.assertEqual(report['net'],'160.00')
        out=bank.write_result(tx,report,self.folder/'out')
        with (out/'ERP_import_2026-09.csv').open(encoding='utf-8-sig',newline='') as handle:
            rows=list(csv.reader(handle))
        self.assertEqual(rows[0],bank.HEADER)
        self.assertEqual(rows[1],['01/09/2026']+['']*6)
        self.assertEqual(rows[-1],['30/09/2026']+['']*6)
        self.assertEqual([r[2] for r in rows[2:-1]],['200.00','-50.00','10.00'])
        self.assertEqual(len(rows),6)
        self.assertEqual(before,{p:hashlib.sha256(p.read_bytes()).hexdigest() for p in (first,second)})

    def test_leap_february_boundary(self):
        p=self.write([['Date','Amount'],['15/02/2024','10']])
        tx,report=bank.prepare(files=[p],profiles=[])
        out=bank.write_result(tx,report,self.folder/'out')
        with (out/'ERP_import_2024-02.csv').open(encoding='utf-8-sig',newline='') as handle:
            rows=list(csv.reader(handle))
        self.assertEqual(rows[1],['01/02/2024']+['']*6)
        self.assertEqual(rows[-1],['29/02/2024']+['']*6)

    def test_cross_month_requires_explicit_resolution(self):
        p=self.write([['Date','Amount'],['30/09/2026','10'],['01/10/2026','20']])
        with self.assertRaises(bank.InputError):
            bank.prepare(files=[p],profiles=[])

    def test_generated_import_cannot_be_reimported_as_raw_statement(self):
        p=self.write()
        tx,report=bank.prepare(files=[p],profiles=[])
        out=bank.write_result(tx,report,self.folder/'out')
        with self.assertRaises(bank.InputError):
            self.parse(out/'ERP_import_2026-09.csv')

    def test_broken_workbook_fails_with_input_error(self):
        for name in ('broken.xls','broken.xlsx'):
            with self.subTest(name=name),self.assertRaises(bank.InputError):
                self.parse(self.text(name,'This is not a workbook'))


    def test_seven_column_segment_statement_synthetic(self):
        rows=[['0','1','BRANCH','0','','','0'],
              ['','000789','ACC-NO','0','','','0'],
              ['1','20260901','OPEN','1000','OPENING','','0'],
              ['1','20260902','TRF','200','RENT','','0'],
              ['1','20260903','CHG','-50','FEE','','0'],
              ['1','20260930','CLOSE','1150','CLOSING','','0']]
        tx,summary=self.parse(self.write(rows))
        self.assertEqual(len(tx),2)
        self.assertEqual(summary['balance_difference'],'0.00')
        self.assertEqual([t.amount for t in tx],[Decimal('200'),Decimal('-50')])

    def test_call_statement_synthetic(self):
        rows=[['0','1','BRANCH','0','','','','0'],
              ['','000789','ACC-NO','0','','','','0'],
              ['HIST','20260901','','1000','','STATEMENT OPENING BALANCE','','0'],
              ['HIST','20260902','','200','RENT','SYNTHETIC REFERENCE','','0'],
              ['HIST','20260903','','-50','FEE','SYNTHETIC FEE','','0'],
              ['HIST','20260930','','1150','','STATEMENT CLOSING BALANCE','','0']]
        tx,summary=self.parse(self.write(rows))
        self.assertEqual(len(tx),2)
        self.assertEqual(summary['balance_difference'],'0.00')
        self.assertEqual(tx[0].description,'RENT')
        self.assertEqual(tx[0].reference,'SYNTHETIC REFERENCE')

    def test_suspected_duplicate_transactions_remain_in_output(self):
        rows=[['Date','Description','Amount'],
              ['01/09/2026','TRANSFER','20'],
              ['01/09/2026','TRANSFER','20']]
        tx,report=bank.prepare(files=[self.write(rows)],profiles=[])
        self.assertEqual(len(tx),2)
        self.assertEqual(report['net'],'40.00')

    def test_binary_xls_preserves_padded_account_and_balances(self):
        path=self.folder/'synthetic.xls'
        path.write_bytes(zlib.decompress(base64.b64decode(SYNTHETIC_XLS_ZLIB_BASE64)))
        tx,summary=self.parse(path)
        self.assertEqual(summary['account'],'000123')
        self.assertEqual(len(tx),2)
        self.assertEqual(summary['net'],'150.00')
        self.assertEqual(summary['balance_difference'],'0.00')
        self.assertEqual([t.amount for t in tx],[Decimal('200'),Decimal('-50')])

if __name__ == '__main__':
    unittest.main(verbosity=2)

