"""Local bank import adapters. All parsers are offline and never execute input content."""
from __future__ import annotations
import csv
import hashlib
import io
import json
import re
import sys
import unicodedata
import zipfile
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

ALLOWED = {'.csv', '.tsv', '.txt', '.xlsx', '.xls', '.ofx', '.qfx', '.xml', '.mt940', '.sta'}
FIELDS = {'date':'日期', 'amount':'有正负号的金额', 'debit':'支出 / Debit', 'credit':'收入 / Credit',
          'description':'交易说明', 'reference':'参考号', 'balance':'交易后余额',
          'direction':'收支标记 DR/CR', 'account':'本账户编号', 'currency':'币种'}
ALIASES = {
 'date': ['date','transaction date','trans date','posting date','posted date','booking date','book date','value date','日期','交易日期','记账日期','入账日期','交易时间','记账时间'],
 'amount': ['amount','transaction amount','trans amount','signed amount','金额','交易金额','发生额','交易额'],
 'debit': ['debit','debits','debit amount','withdrawal','withdrawals','money out','paid out','payment amount','支出','借方','借方金额','付款金额','取款金额'],
 'credit': ['credit','credits','credit amount','deposit','deposits','money in','paid in','receipt amount','收入','贷方','贷方金额','收款金额','存款金额'],
 'description': ['description','transaction description','transaction details','details','narration','narrative','particulars','memo','payee','merchant','description 1','description 2','摘要','交易摘要','交易说明','说明','用途','备注','对方户名','交易对方'],
 'reference': ['reference','ref','reference number','payment reference','transaction reference','transaction id','bank reference','cheque number','check number','cheque no','参考号','交易流水号','银行流水号','流水号','凭证号','支票号'],
 'balance': ['balance','running balance','account balance','closing balance','余额','账户余额','交易后余额','结余'],
 'direction': ['debit credit','debit/credit','dr/cr','cr/dr','d/c','credit debit indicator','transaction direction','收支','收支标志','借贷标志','借贷方向','交易方向'],
 'account': ['account','account number','account no','account id','账户','账号','账户号码','本方账号','本方账户'],
 'currency': ['currency','ccy','currency code','币种','货币'],
}
OPEN_LABELS = {'opening balance','balance brought forward','balance b/f','balance bf','beginning balance','statement opening balance','期初余额','上期余额','上期结余'}
CLOSE_LABELS = {'closing balance','ending balance','balance carried forward','balance c/f','balance cf','statement closing balance','期末余额','本期结余'}
FOOTER_LABELS = {'total','totals','total debit','total credit','total debits','total credits','合计','总计'}

class FormatError(ValueError):
    pass

def normal(value):
    value = unicodedata.normalize('NFKC', str(value)).casefold().strip().lstrip('\ufeff')
    return re.sub(r'[\s_\-./:()（）]+', '', value)

LOOKUP = {normal(alias): role for role, aliases in ALIASES.items() for alias in aliases}

def decode_text(raw):
    candidates = ['utf-8-sig']
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')) or b'\x00' in raw[:100]:
        candidates.insert(0, 'utf-16')
    candidates += ['gb18030', 'cp1252']
    for encoding in candidates:
        try:
            return raw.decode(encoding), encoding
        except (UnicodeError, LookupError):
            pass
    raise FormatError('无法识别文本编码，请导出 UTF-8 CSV')

def text_table(path):
    raw = Path(path).read_bytes()
    text, encoding = decode_text(raw)
    sample = '\n'.join(text.splitlines()[:80])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = '\t' if Path(path).suffix.lower() == '.tsv' else ','
        # Metadata before a table can confuse Sniffer. Prefer a recognizable header.
        for line in text.splitlines()[:80]:
            scored = []
            for candidate in (',',';','\t','|'):
                row = next(csv.reader([line], delimiter=candidate))
                roles = {LOOKUP.get(normal(c)) for c in row}
                if 'date' in roles and roles.intersection({'amount','debit','credit'}):
                    scored.append((len(roles - {None}), candidate))
            if scored:
                delimiter = max(scored)[1]
                break
    try:
        rows = list(csv.reader(io.StringIO(text, newline=''), delimiter=delimiter, strict=True))
    except csv.Error as exc:
        raise FormatError(f'CSV 引号或分隔符格式错误：{exc}') from exc
    if rows and len(rows[0]) == 1 and rows[0][0].strip().lower().startswith('sep='):
        # Preserve physical row numbers by making the directive an empty metadata row.
        marker = rows[0][0].strip()[4:]
        if marker in ',;\t|':
            rows = [[]] + list(csv.reader(io.StringIO('\n'.join(text.splitlines()[1:])), delimiter=marker, strict=True))
    return rows, hashlib.sha256(raw).hexdigest(), encoding

def parse_money(value, label='金额'):
    if isinstance(value, Decimal):
        result = value
    else:
        s = unicodedata.normalize('NFKC', str(value)).strip().replace('\u2212','-')
        if not s or s in ('-', '—', '–'):
            return None
        direction = None
        matched = re.search(r'\s*(DR|CR)\s*$', s, re.I)
        if matched:
            direction = matched.group(1).upper()
            s = s[:matched.start()].strip()
        negative = s.startswith('(') and s.endswith(')')
        if negative:
            s = s[1:-1].strip()
        if s.endswith('-'):
            negative, s = True, s[:-1]
        s = re.sub(r'^([+-]?)(?:ZAR|USD|EUR|GBP|CNY|RMB|R|\$|€|£|¥)\s*', r'\1', s, flags=re.I)
        s = re.sub(r'\s*(?:ZAR|USD|EUR|GBP|CNY|RMB)\s*$', '', s, flags=re.I)
        s = re.sub(r'[\s\u00a0\u202f\u2009\']', '', s)
        if ',' in s and '.' in s:
            decimal_mark, group_mark = (',','.') if s.rfind(',') > s.rfind('.') else ('.',',')
            whole, fractional = s.rsplit(decimal_mark, 1)
            if not re.fullmatch(r'[+-]?\d{1,3}(?:' + re.escape(group_mark) + r'\d{3})*|[+-]?\d+', whole) or not re.fullmatch(r'\d{1,2}', fractional):
                raise FormatError(f'{label}：千位或小数格式无效：{value!r}')
            s = whole.replace(group_mark,'') + '.' + fractional
        elif ',' in s:
            if re.fullmatch(r'[+-]?\d{1,3}(?:,\d{3})+', s):
                s = s.replace(',','')
            elif re.fullmatch(r'[+-]?\d+,\d{1,2}', s):
                s = s.replace(',','.')
            else:
                raise FormatError(f'{label}：逗号金额格式不明确：{value!r}')
        if not re.fullmatch(r'[+-]?\d+(?:\.\d{1,2})?', s):
            raise FormatError(f'{label}：无法识别金额：{value!r}')
        result = Decimal(s)
        if negative:
            if result < 0:
                raise FormatError(f'{label}：存在重复负号：{value!r}')
            result = -result
        if direction:
            if direction == 'CR' and result < 0:
                raise FormatError(f'{label}：负号与 CR 冲突')
            result = -abs(result) if direction == 'DR' else abs(result)
    if not result.is_finite() or result != result.quantize(Decimal('.01')):
        raise FormatError(f'{label}：金额必须精确到分')
    return result.quantize(Decimal('.01'))

def parse_date(value, order='DMY'):
    s = str(value).strip()
    if not s:
        raise FormatError('日期为空')
    if re.fullmatch(r'0\d{8}', s):
        s = s[1:]
    if re.fullmatch(r'(?:19|20)\d{6}', s):
        return datetime.strptime(s, '%Y%m%d')
    if re.fullmatch(r'\d{5}(?:\.0+)?', s) and 20000 <= float(s) <= 80000:
        return datetime(1899,12,30) + timedelta(days=int(float(s)))
    if re.match(r'^\d{4}[-/]\d{1,2}[-/]\d{1,2}', s):
        match = re.match(r'^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[ T].*)?$', s)
        if match:
            return datetime(*map(int, match.groups()))
    match = re.match(r'^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2}|\d{4})(?:[ T].*)?$', s)
    if match:
        a,b,y = map(int,match.groups())
        y = y + (2000 if y < 69 else 1900) if y < 100 else y
        if order == 'AUTO':
            if a <= 12 and b <= 12 and a != b:
                raise FormatError(f'日期 {s} 的日/月顺序不明确，请选择 DMY 或 MDY')
            order = 'DMY' if a > 12 or a == b else 'MDY'
        return datetime(y, b, a) if order == 'DMY' else datetime(y, a, b)
    english_months = {name:i for i,names in enumerate([
        ('jan','january'),('feb','february'),('mar','march'),('apr','april'),('may',),('jun','june'),
        ('jul','july'),('aug','august'),('sep','sept','september'),('oct','october'),('nov','november'),('dec','december')],1) for name in names}
    match = re.fullmatch(r'(\d{1,2})[\s/-]+([A-Za-z]+)[\s/,-]+(\d{2}|\d{4})',s)
    reverse = re.fullmatch(r'([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})',s)
    if match or reverse:
        d,m,y = match.groups() if match else (reverse[2],reverse[1],reverse[3])
        y = int(y) + 2000 if len(y)==2 else int(y)
        if m.casefold() in english_months:
            return datetime(y,english_months[m.casefold()],int(d))
    raise FormatError(f'无法识别日期 {s!r}；支持 YYYYMMDD、日/月/年、年/月/日、英文月份及 Excel 日期')

def local_name(tag):
    return tag.rsplit('}',1)[-1]

def spreadsheet_tables(path):
    """Read XLSX via standard OOXML, real XLS via bundled pure-Python xlrd."""
    path = Path(path)
    raw = path.read_bytes()
    if raw.startswith(b'PK'):
        with zipfile.ZipFile(io.BytesIO(raw)) as book:
            names = book.namelist()
            strings = []
            if 'xl/sharedStrings.xml' in names:
                strings = [''.join(n.itertext()) for n in ET.fromstring(book.read('xl/sharedStrings.xml'))]
            workbook = ET.fromstring(book.read('xl/workbook.xml'))
            date1904 = any(local_name(e.tag)=='workbookPr' and e.get('date1904') in ('1','true') for e in workbook.iter())
            rels = {r.get('Id'):r.get('Target') for r in ET.fromstring(book.read('xl/_rels/workbook.xml.rels'))}
            date_styles = set()
            zero_styles = {}
            if 'xl/styles.xml' in names:
                styles = ET.fromstring(book.read('xl/styles.xml'))
                formats = {int(e.get('numFmtId')):e.get('formatCode','') for e in styles.iter() if local_name(e.tag)=='numFmt'}
                cellxfs = next((e for e in styles if local_name(e.tag)=='cellXfs'), [])
                for i,xf in enumerate(cellxfs):
                    number = int(xf.get('numFmtId','0'))
                    fmt = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', '', formats.get(number,''))
                    if number in set(range(14,23))|set(range(27,37))|set(range(45,48))|set(range(50,59)) or re.search(r'[yd]',fmt,re.I):
                        date_styles.add(i)
                    if re.fullmatch(r'0{2,}',formats.get(number,'')):
                        zero_styles[i]=len(formats[number])
            tables = []
            for sheet in (e for e in workbook.iter() if local_name(e.tag)=='sheet'):
                rid = next((v for k,v in sheet.attrib.items() if k.endswith('}id')),None)
                target = rels.get(rid,'')
                location = target.lstrip('/') if target.startswith('/') else 'xl/' + target
                if '..' in Path(location).parts or location not in names:
                    raise FormatError('Excel 工作表关系无效')
                rows=[]
                for r in (e for e in ET.fromstring(book.read(location)).iter() if local_name(e.tag)=='row'):
                    values={}
                    rowno=int(r.get('r',len(rows)+1))
                    while len(rows)<rowno-1:
                        rows.append([])
                    for cell in r:
                        if local_name(cell.tag)!='c': continue
                        column=re.match('[A-Z]+',cell.get('r','A1'))[0]
                        index=0
                        for letter in column: index=index*26+ord(letter)-64
                        value=next((e.text or '' for e in cell if local_name(e.tag)=='v'),'')
                        kind=cell.get('t','')
                        formula=any(local_name(e.tag)=='f' for e in cell)
                        if kind=='s': value=strings[int(value)] if value else ''
                        elif kind=='inlineStr': value=''.join(e.text or '' for e in cell.iter() if local_name(e.tag)=='t')
                        elif kind=='e': raise FormatError(f'Excel {sheet.get("name")}!{cell.get("r")} 包含错误值 {value}')
                        elif formula and not value: raise FormatError(f'Excel {cell.get("r")} 的公式没有已保存结果，请先在 Excel/WPS 计算并保存')
                        elif int(cell.get('s','0')) in date_styles and value:
                            epoch=datetime(1904,1,1) if date1904 else datetime(1899,12,30)
                            value=(epoch+timedelta(days=float(value))).strftime('%Y-%m-%d')
                        elif int(cell.get('s','0')) in zero_styles and re.fullmatch(r'\d+',value):
                            value=value.zfill(zero_styles[int(cell.get('s','0'))])
                        values[index-1]=str(value)
                    rows.append([values.get(i,'') for i in range(max(values,default=-1)+1)])
                tables.append((sheet.get('name','Sheet'),rows))
            return tables
    if raw.startswith(b'\xd0\xcf\x11\xe0'):
        sys.path.insert(0,str(Path(__file__).resolve().parent/'vendor'))
        try: import xlrd
        except ImportError as exc: raise FormatError('缺少 XLS 读取组件，请复制完整工具文件夹（包括 vendor）') from exc
        try:
            book=xlrd.open_workbook(file_contents=raw,on_demand=True,formatting_info=True)
            tables=[]
            for name in book.sheet_names():
                sheet=book.sheet_by_name(name)
                rows=[]
                for r in range(sheet.nrows):
                    values=[]
                    for c in range(sheet.ncols):
                        cell=sheet.cell(r,c)
                        if cell.ctype==xlrd.XL_CELL_DATE: value=xlrd.xldate_as_datetime(cell.value,book.datemode).strftime('%Y-%m-%d')
                        elif cell.ctype==xlrd.XL_CELL_ERROR: raise FormatError(f'Excel {name} 第 {r+1} 行包含错误值')
                        elif cell.ctype==xlrd.XL_CELL_EMPTY: value=''
                        elif cell.ctype==xlrd.XL_CELL_NUMBER:
                            value=format(cell.value,'.15g')
                            format_code=book.format_map[book.xf_list[cell.xf_index].format_key].format_str
                            if re.fullmatch(r'0{2,}',format_code) and re.fullmatch(r'\d+',value):value=value.zfill(len(format_code))
                        else: value=str(cell.value)
                        values.append(value)
                    rows.append(values)
                tables.append((name,rows))
            book.release_resources()
            return tables
        except FormatError: raise
        except Exception as exc: raise FormatError(f'无法读取 XLS：{exc}') from exc
    text, _ = decode_text(raw)
    if '<table' in text.lower():
        class TableReader(HTMLParser):
            def __init__(self): super().__init__(); self.rows=[]; self.row=None; self.cell=None
            def handle_starttag(self,tag,attrs):
                if tag=='tr': self.row=[]
                if tag in ('td','th') and self.row is not None: self.cell=[]
            def handle_data(self,data):
                if self.cell is not None: self.cell.append(data)
            def handle_endtag(self,tag):
                if tag in ('td','th') and self.cell is not None: self.row.append(''.join(self.cell).strip());self.cell=None
                if tag=='tr' and self.row is not None: self.rows.append(self.row);self.row=None
        reader=TableReader();reader.feed(text)
        return [('HTML table',reader.rows)]
    if '<Workbook' in text or ':Workbook' in text:
        root=ET.fromstring(text);tables=[]
        for sheet in (e for e in root.iter() if local_name(e.tag)=='Worksheet'):
            rows=[]
            for r in (e for e in sheet.iter() if local_name(e.tag)=='Row'):
                values=[]
                for cell in r:
                    if local_name(cell.tag)!='Cell': continue
                    index=next((int(v) for k,v in cell.attrib.items() if local_name(k)=='Index'),len(values)+1)
                    while len(values)<index-1: values.append('')
                    values.append(''.join(cell.itertext()).strip())
                rows.append(values)
            tables.append((next((v for k,v in sheet.attrib.items() if local_name(k)=='Name'),'Sheet'),rows))
        return tables
    # Some exports are CSV/TSV text with an .xls extension.
    return [('Text export',text_table(path)[0])]

def load_tables(path):
    path=Path(path)
    return spreadsheet_tables(path) if path.suffix.lower() in ('.xls','.xlsx') else [('CSV',text_table(path)[0])]

def read_profiles(path):
    if not Path(path).exists(): return []
    try:
        obj=json.loads(Path(path).read_text(encoding='utf-8'))
        return obj.get('profiles',[])
    except (OSError,ValueError) as exc: raise FormatError(f'无法读取自定义列规则：{exc}') from exc

def signature(row): return [normal(c) for c in row]

def infer_columns(row):
    result={}
    for i,cell in enumerate(row):
        role=LOOKUP.get(normal(cell))
        if role:
            if role == 'date' and role in result:
                priorities = {normal(v): n for n, v in enumerate(('booking date','posting date','transaction date','date','value date'))}
                old = result[role][0]
                if priorities.get(normal(cell), 2) < priorities.get(normal(row[old]),2): result[role]=[i]
                continue
            if role in result and role not in ('description','reference'):
                raise FormatError(f'表头中有多列都被识别为 {FIELDS[role]}，请设置列对应，明确使用哪一列')
            result.setdefault(role,[]).append(i)
    return result

def valid_columns(columns):
    return bool(columns.get('date') and (columns.get('amount') or columns.get('debit') or columns.get('credit')))

def find_table(path, profiles):
    tables=load_tables(path)
    candidates=[]
    for sheet,rows in tables:
        for i,row in enumerate(rows[:100]):
            matches=[p for p in profiles if p.get('signature')==signature(row) and (not p.get('sheet') or p['sheet']==sheet)]
            if matches:
                p=matches[-1]
                columns={k:([v] if isinstance(v,int) else v) for k,v in p['columns'].items() if v is not None}
                candidates.append((sheet,rows,i,columns,p.get('date_order'),True))
                break
            columns=infer_columns(row)
            if valid_columns(columns):
                candidates.append((sheet,rows,i,columns,None,False))
                break
    saved=[c for c in candidates if c[-1]]
    if saved: candidates=saved
    if len(candidates)>1:
        raise FormatError('有多个可用工作表：'+ '、'.join(c[0] for c in candidates)+'。请在“设置列对应”中指定工作表')
    if not candidates:
        raise FormatError('未识别到日期和金额列。可在“设置列对应”里指定表头行及字段，并保存规则')
    return candidates[0]

def finish(path, entries, account='', opening=None, closing=None, balances=None, format_name='', notes=None, currency='', **extra):
    notes=list(notes or [])
    account=account.strip() or '未提供账户：'+Path(path).name
    for e in entries: e['account']=account
    net=sum((e['amount'] for e in entries),Decimal('0'))
    difference=None
    if opening is not None and closing is not None:
        difference=opening+net-closing
        if difference: raise FormatError(f'期初＋交易净额－期末＝{difference:.2f}，余额不平')
        validation='期初期末余额核平'
    else:
        validation='缺少完整期初期末余额，未核平'
        notes.append('原文件未提供完整期初期末余额；不会把交易金额合计当作独立余额校验。')
    if balances:
        present=[(i,b) for i,b in enumerate(balances) if b is not None]
        dates=[e['date'] for e in entries]
        reverse=all(a>=b for a,b in zip(dates,dates[1:]))
        forward=all(a<=b for a,b in zip(dates,dates[1:]))
        if present and (forward or reverse):
            prefix=[Decimal('0')]
            for entry in entries:prefix.append(prefix[-1]+entry['amount'])
            accepted=[]
            for backwards in ([False] if forward and not reverse else [True] if reverse and not forward else [False,True]):
                good=True
                for (i,old),(j,new) in zip(present,present[1:]):
                    delta=(prefix[j]-prefix[i]) if backwards else (prefix[j+1]-prefix[i+1])
                    if (old-delta if backwards else old+delta)!=new:good=False
                for i,balance in present:
                    since_open=(prefix[-1]-prefix[i]) if backwards else prefix[i+1]
                    if opening is not None and opening+since_open!=balance:good=False
                    if closing is not None and closing-(net-since_open)!=balance:good=False
                if good:accepted.append(backwards)
            if not accepted:raise FormatError('交易后余额与交易金额或期初期末余额不一致')
            if difference is None:
                validation='逐行余额链核对通过；缺少独立期初或期末余额' if len(present)>1 else '只有一条交易后余额，无法完成余额链核对'
        elif present:
            notes.append('余额列不足两条或日期顺序混合，无法完成完整逐行余额链核对。')
    return entries, {'file':Path(path).name,'account':account,'count':len(entries),
        'opening':format(opening,'.2f') if opening is not None else None,
        'closing':format(closing,'.2f') if closing is not None else None,'net':format(net,'.2f'),
        'balance_difference':format(difference,'.2f') if difference is not None else None,
        'sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest(),'format':format_name,
        'validation':validation,'validation_notes':notes,'currency':currency,**extra}

def generic_statement(path, profiles=(), date_order='DMY'):
    sheet,rows,header_index,columns,profile_order,saved=find_table(path,profiles)
    date_order=profile_order or date_order
    entries=[];balances=[];accounts=set();currencies=set();notes=[];opening=closing=None
    def cell(row,role):
        return ' / '.join(row[i].strip() for i in columns.get(role,[]) if i<len(row) and row[i].strip())
    known={i for indices in columns.values() for i in indices}
    for n,row in enumerate(rows[header_index+1:],header_index+2):
        if not any(c.strip() for c in row):continue
        if signature(row)==signature(rows[header_index]):continue
        label=f'{Path(path).name} 第 {n} 行'
        desc=cell(row,'description');ref=cell(row,'reference')
        date_text=cell(row,'date')
        labels={normal(c) for c in row if c.strip()}
        is_open=bool(labels.intersection(map(normal,OPEN_LABELS)))
        is_close=bool(labels.intersection(map(normal,CLOSE_LABELS)))
        if is_open or is_close:
            amount=parse_money(cell(row,'balance') or cell(row,'amount'),label)
            if amount is None: raise FormatError(f'{label}：期初或期末记录没有可识别余额')
            if is_open:
                if opening is not None:raise FormatError(f'{label}：存在多个期初余额，请使用分段格式或调整列规则')
                opening=amount
            else:
                if closing is not None:raise FormatError(f'{label}：存在多个期末余额')
                closing=amount
            continue
        if labels.intersection(map(normal,FOOTER_LABELS)) and (not date_text or normal(date_text) in set(map(normal,FOOTER_LABELS))):
            notes.append(f'第 {n} 行为明确标记的合计行，未当作交易。');continue
        # A date-only IQ boundary row isn't a real transaction.
        amount_text=cell(row,'amount');debit_text=cell(row,'debit');credit_text=cell(row,'credit')
        if date_text and not any((amount_text,debit_text,credit_text,desc,ref,cell(row,'balance'))):
            parse_date(date_text,date_order);continue
        if not date_text:
            raise FormatError(f'{label}：表头之后有非空行但缺少日期；请检查备注、合计或表头行设置，未静默忽略')
        try:date=parse_date(date_text,date_order)
        except (ValueError,OverflowError) as exc:raise FormatError(f'{label}：{exc}') from exc
        if columns.get('amount'):
            amount=parse_money(amount_text,label)
            if amount is None:raise FormatError(f'{label}：缺少交易金额')
            if debit_text or credit_text:
                debit=parse_money(debit_text,label) or Decimal('0')
                credit=parse_money(credit_text,label) or Decimal('0')
                if debit and credit:raise FormatError(f'{label}：收入与支出都不为零')
                directed=abs(credit)-abs(debit)
                if abs(amount)!=abs(directed) or (amount<0 and directed>0):raise FormatError(f'{label}：金额列与收入支出列不一致')
                amount=directed
        else:
            debit=parse_money(debit_text,label) or Decimal('0')
            credit=parse_money(credit_text,label) or Decimal('0')
            if not debit_text and not credit_text:raise FormatError(f'{label}：收入和支出均为空')
            if debit and credit:raise FormatError(f'{label}：同一行收入与支出都不为零，需要核实')
            amount=abs(credit)-abs(debit)
        direction=normal(cell(row,'direction'))
        if direction:
            if direction in {'d','dr','debit','借','借方','支出','付款','withdrawal'}:amount=-abs(amount)
            elif direction in {'c','cr','credit','贷','贷方','收入','收款','deposit'}:
                if amount<0:raise FormatError(f'{label}：负金额与收入标记冲突')
                amount=abs(amount)
            else:raise FormatError(f'{label}：无法识别收支标记 {cell(row,"direction")!r}')
        account=cell(row,'account')
        if account:accounts.add(account)
        currency=cell(row,'currency').upper()
        if currency:currencies.add(currency)
        if len(accounts)>1:raise FormatError('同一表格包含多个账户，请按账户分别导出后再合并，避免混淆余额校验')
        if len(currencies)>1:raise FormatError('同一表格包含多个币种，不能直接合并金额')
        balance=parse_money(cell(row,'balance'),label) if columns.get('balance') else None
        entries.append({'source_row':n,'date':date,'amount':amount,'description':desc,'reference':ref})
        balances.append(balance)
    extras=[str(c) for i,c in enumerate(rows[header_index]) if i not in known and str(c).strip()]
    if extras:notes.append('未映射到 IQ 的原始列：'+'、'.join(extras)+'；请确认是否需要在“设置列对应”里加入说明或参考号。')
    if not accounts:notes.append('未提供本账户编号，来源以文件名区分，无法仅凭账号排除重复导出。')
    return finish(path,entries,next(iter(accounts),''),opening,closing,balances,
                  f'通用表格 / {sheet} / '+('自定义列规则' if saved else '自动表头识别'),notes,
                  next(iter(currencies),''),sheet=sheet,header_row=header_index+1,date_order=date_order,
                  column_mapping={role:[i+1 for i in indices] for role,indices in columns.items()})

def ofx_statement(path):
    text,_=decode_text(Path(path).read_bytes())
    statements=re.findall(r'<(?:STMTRS|CCSTMTRS)\b[^>]*>(.*?)</(?:STMTRS|CCSTMTRS)>',text,re.I|re.S)
    if len(statements)!=1:raise FormatError('OFX 必须含一个银行或信用卡账户；请按账户导出')
    statement=statements[0]
    def tag(block,name):
        m=re.search(r'<'+name+r'\b[^>]*>\s*([^<\r\n]+)',block,re.I)
        return unescape(m[1].strip()) if m else ''
    account=tag(statement,'ACCTID');currency=tag(statement,'CURDEF');entries=[]
    blocks=re.findall(r'<STMTTRN\b[^>]*>(.*?)</STMTTRN>',statement,re.I|re.S)
    for n,block in enumerate(blocks,1):
        date=tag(block,'DTPOSTED');amount=parse_money(tag(block,'TRNAMT'),'OFX金额')
        if not re.match(r'\d{8}',date) or amount is None:raise FormatError(f'OFX 第 {n} 笔缺少日期或金额')
        entries.append({'source_row':n,'date':parse_date(date[:8]),'amount':amount,
                        'description':' / '.join(x for x in (tag(block,'NAME'),tag(block,'MEMO')) if x),
                        'reference':tag(block,'FITID') or tag(block,'CHECKNUM')})
    pending=re.findall(r'<STMTTRNP\b',statement,re.I)
    if pending:raise FormatError('OFX 包含待入账交易，请导出已入账流水')
    ledger=re.search(r'<LEDGERBAL\b[^>]*>(.*?)</LEDGERBAL>',statement,re.I|re.S)
    closing=parse_money(tag(ledger[1],'BALAMT')) if ledger else None
    return finish(path,entries,account,closing=closing,format_name='OFX/QFX 银行流水',currency=currency,
                  notes=['OFX 没有独立期初余额，不能完整核平；FITID 用作参考号。'])

def camt_statement(path):
    root=ET.fromstring(Path(path).read_bytes())
    statements=[e for e in root.iter() if local_name(e.tag)=='Stmt']
    if len(statements)!=1:raise FormatError('camt XML 必须包含一个 Stmt；请按账户导出')
    stmt=statements[0]
    def children(node,name):return [e for e in node if local_name(e.tag)==name]
    def descend(node,names):
        for name in names.split('/'):
            node=next((e for e in node if local_name(e.tag)==name),None)
            if node is None:return ''
        return (node.text or '').strip()
    acct=next(iter(children(stmt,'Acct')),None)
    account=(descend(acct,'Id/IBAN') or descend(acct,'Id/Othr/Id')) if acct is not None else ''
    currencies=set();entries=[];opening=closing=None
    def signed(node):
        am=next(iter(children(node,'Amt')),None)
        if am is None:raise FormatError('camt 记录缺少 Amt')
        currency=am.get('Ccy','')
        if currency:currencies.add(currency)
        amount=parse_money(am.text or '')
        code=descend(node,'CdtDbtInd')
        if amount is None or code not in ('CRDT','DBIT'):raise FormatError('camt 金额或收支方向无效')
        if amount<0:raise FormatError('camt Amt 应为非负值，方向应由 CdtDbtInd 指定')
        return amount if code=='CRDT' else -amount
    for bal in children(stmt,'Bal'):
        kind=descend(bal,'Tp/CdOrPrtry/Cd')
        if kind in ('OPBD','CLBD'):
            value=signed(bal)
            if kind=='OPBD':
                if opening is not None:raise FormatError('camt 包含多个 OPBD 余额')
                opening=value
            else:
                if closing is not None:raise FormatError('camt 包含多个 CLBD 余额')
                closing=value
    for n,entry in enumerate(children(stmt,'Ntry'),1):
        status=descend(entry,'Sts/Cd') or descend(entry,'Sts')
        if status and status!='BOOK':raise FormatError(f'camt 第 {n} 项状态为 {status}，仅支持已入账 BOOK')
        date=descend(entry,'BookgDt/Dt') or descend(entry,'BookgDt/DtTm') or descend(entry,'ValDt/Dt')
        if not date:raise FormatError('camt 交易缺少入账日期')
        description=[];references=[]
        for item in entry.iter():
            name=local_name(item.tag);value=(item.text or '').strip()
            if value and name in ('Ustrd','AddtlNtryInf','AddtlTxInf') and value not in description:description.append(value)
            if value and name in ('AcctSvcrRef','EndToEndId','NtryRef') and value not in references and value!='NOTPROVIDED':references.append(value)
        entries.append({'source_row':n,'date':parse_date(date),'amount':signed(entry),
                        'description':' / '.join(description),'reference':' / '.join(references)})
    if len(currencies)>1:raise FormatError('camt 记录含多个币种，不能直接合并金额')
    return finish(path,entries,account,opening,closing,format_name='ISO 20022 camt.053 XML',currency=next(iter(currencies),''))

def mt940_statement(path):
    text,_=decode_text(Path(path).read_bytes())
    fields=[]
    for line in text.splitlines():
        m=re.match(r'^:(\d{2}[A-Z]?):(.*)',line)
        if m:fields.append([m[1],m[2]])
        elif fields and not line.startswith(('{','-}')):fields[-1][1]+='\n'+line
    accounts={v.strip() for k,v in fields if k=='25'}
    if len(accounts)!=1:raise FormatError('MT940 必须只包含一个账户')
    entries=[];opening=closing=None;active=None;currencies=set();segments=[]
    def balance(value):
        m=re.match(r'^([CD])(\d{6})([A-Z]{3})(\d+(?:,\d*)?)',value.strip())
        if not m:raise FormatError('MT940 余额字段格式无法识别')
        currencies.add(m[3]);value=parse_money(m[4].rstrip(','))
        return value if m[1]=='C' else -value
    for tag,value in fields:
        if tag in ('60F','60M'):
            b=balance(value)
            if active is not None or (closing is not None and b!=closing):raise FormatError('MT940 分段余额不连续')
            if opening is None:opening=b
            active=[b,Decimal('0')]
        elif tag=='61':
            if active is None:raise FormatError('MT940 交易缺少前置期初余额')
            m=re.match(r'^(\d{6})(\d{4})?(R?[DC])([A-Z])?(\d+(?:,\d*)?)(.*)',value,re.S)
            if not m:raise FormatError('MT940 :61: 记录结构无法识别')
            date=datetime.strptime(m[1],'%y%m%d')
            if m[2]:
                mm,dd=int(m[2][:2]),int(m[2][2:]);year=date.year
                if date.month==12 and mm==1:year+=1
                elif date.month==1 and mm==12:year-=1
                date=datetime(year,mm,dd)
            amount=parse_money(m[5].rstrip(','));indicator=m[3]
            amount=-amount if indicator in ('D','RC') else amount
            active[1]+=amount
            reference=m[6].strip()
            entries.append({'source_row':len(entries)+1,'date':date,'amount':amount,'description':'','reference':reference})
        elif tag=='86' and entries:entries[-1]['description']=value.strip()
        elif tag in ('62F','62M'):
            b=balance(value)
            if active is None or active[0]+active[1]!=b:raise FormatError('MT940 分段期初＋交易净额与期末不一致')
            closing=b;active=None;segments.append(format(b,'.2f'))
    if active is not None or opening is None or closing is None:raise FormatError('MT940 缺少完整的期初或期末余额')
    if len(currencies)>1:raise FormatError('MT940 含多个币种，不能直接合并')
    return finish(path,entries,next(iter(accounts)),opening,closing,format_name='SWIFT MT940',currency=next(iter(currencies),''),segments_checked=len(segments))

def parse_adaptive(path, profiles=(), date_order='DMY'):
    path=Path(path)
    try:
        raw=path.read_bytes()
        head=raw[:3000].decode('utf-8',errors='ignore')
        suffix=path.suffix.lower()
        if suffix in ('.ofx','.qfx') or re.search(r'<OFX[>\s]',head,re.I):return ofx_statement(path)
        if suffix in ('.mt940','.sta') or re.search(r'(?m)^:60[FM]:',head):return mt940_statement(path)
        if suffix=='.xml' and ('<Document' in head or 'camt.' in head):return camt_statement(path)
        return generic_statement(path,profiles,date_order)
    except FormatError:raise
    except (ValueError,InvalidOperation,ET.ParseError,zipfile.BadZipFile,KeyError,IndexError) as exc:
        raise FormatError(f'{path.name}：格式或内容不符合解析要求：{exc}') from exc
