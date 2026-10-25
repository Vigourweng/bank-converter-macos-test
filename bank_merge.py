"""Merge local bank statements into the user's IQ import layout.

Offline; XLS reader is bundled. Run without arguments for the desktop interface.
"""
from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import io
import json
import os
import subprocess
import sys
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from itertools import zip_longest
import bank_formats

RULES_PATH = Path(__file__).resolve().with_name('format_rules.json')
UI_FONT = 'Helvetica' if sys.platform == 'darwin' else 'Microsoft YaHei UI'

SOURCES = ()
HEADER = ['Date', 'SERVICE FEE', 'Amount', 'DESCRIPTION', 'REFERENCE', 'Balance', 'CHEQUE NUMBER']
ZERO = Decimal('0.00')


class InputError(ValueError):
    pass


def money(value: str, label: str) -> Decimal:
    if not re.fullmatch(r'-?\d+(?:\.\d{1,2})?', value.strip()):
        raise InputError(f'{label}：金额格式无效：{value!r}')
    try:
        result = Decimal(value.strip())
    except InvalidOperation as exc:
        raise InputError(f'{label}：金额无效') from exc
    return result.quantize(Decimal('0.01'))


def read_csv(path: Path):
    try:
        return bank_formats.text_table(path)
    except bank_formats.FormatError as exc:
        raise InputError(f'{path.name}：{exc}') from exc


@dataclass
class Transaction:
    source: str
    source_row: int
    account: str
    date: datetime
    amount: Decimal
    description: str
    reference: str

    def output_row(self):
        return [self.date.strftime('%d/%m/%Y'), '', format(self.amount, '.2f'),
                self.description, self.reference, '', '']

    def key(self):
        return (self.date.strftime('%Y-%m-%d'), self.amount, self.description.rstrip(), self.reference.rstrip())


def bank_date(value, label):
    value = value.strip()
    if re.fullmatch(r'0\d{8}', value):
        value = value[1:]
    if not re.fullmatch(r'\d{8}', value):
        raise InputError(f'{label}：日期必须是 YYYYMMDD 或前导零加 YYYYMMDD')
    try:
        return datetime.strptime(value, '%Y%m%d')
    except ValueError as exc:
        raise InputError(f'{label}：无效日期 {value!r}') from exc


def source_summary(path, account, opening, closing, transactions, digest, encoding,
                   ignored_blank_rows, format_name, **extra):
    net = sum((t.amount for t in transactions), ZERO)
    difference = opening + net - closing
    if difference != ZERO:
        raise InputError(f'{path.name}：期初＋交易净额－期末＝{difference:.2f}，余额不平，未生成导入文件')
    return transactions, {
        'file': path.name, 'account': account, 'count': len(transactions),
        'opening': format(opening, '.2f'), 'closing': format(closing, '.2f'),
        'net': format(net, '.2f'), 'balance_difference': format(difference, '.2f'),
        'sha256': digest, 'encoding': encoding, 'ignored_blank_rows': ignored_blank_rows,
        'format': format_name, **extra,
    }


def parse_source(path: Path, *, profiles=None, date_order='DMY'):
    path = Path(path)
    if path.suffix.lower() in ('.xls','.xlsx'):
        try: tables = bank_formats.load_tables(path)
        except (bank_formats.FormatError,ValueError) as exc: raise InputError(str(exc)) from exc
        if len(tables)==1:
            rows=tables[0][1]
            nonempty=[r for r in rows if any(c.strip() for c in r)]
            legacy=any(len(r)>=3 and r[2].strip()=='BRANCH' for r in nonempty)
            if legacy:
                digest=hashlib.sha256(path.read_bytes()).hexdigest()
                if {len(r) for r in nonempty}=={7}:return parse_seven_column(path,rows,digest,'Excel')
                if {len(r) for r in nonempty}=={8} and any(r[0].strip()=='HIST' and r[5].strip().upper()=='STATEMENT OPENING BALANCE' for r in nonempty):return parse_call_statement(path,rows,digest,'Excel')
                return parse_hist_statement(path,(rows,digest,'Excel'))
    if path.suffix.lower() in ('.csv','.tsv','.txt'):
        rows, digest, encoding = read_csv(path)
        nonempty = [(n, r) for n, r in enumerate(rows, 1) if any(c.strip() for c in r)]
        widths = {len(r) for _, r in nonempty}
        if nonempty and nonempty[0][1][:7] == HEADER:
            raise InputError(f'{path.name}：这是 IQ 导入成品，不应再次当作原始银行流水合并')
        legacy = any(len(r)>=3 and r[2].strip()=='BRANCH' for _,r in nonempty)
        if legacy:
            if widths == {7}:
                return parse_seven_column(path, rows, digest, encoding)
            if widths == {8} and any(r[0].strip() == 'HIST' and r[5].strip().upper() == 'STATEMENT OPENING BALANCE' for _, r in nonempty):
                return parse_call_statement(path, rows, digest, encoding)
            return parse_hist_statement(path)
    try:
        profiles = bank_formats.read_profiles(RULES_PATH) if profiles is None else profiles
        entries, summary = bank_formats.parse_adaptive(path, profiles, date_order)
        tx = [Transaction(path.name, e['source_row'], e['account'], e['date'], e['amount'],
                          e['description'], e['reference']) for e in entries]
        return tx, summary
    except bank_formats.FormatError as exc:
        raise InputError(f'{path.name}：{exc}') from exc


def parse_seven_column(path, rows, digest, encoding):
    account = None
    branch = False
    transactions = []
    segments = []
    active = None
    first_opening = last_closing = None
    blanks = 0
    fee_control = ZERO
    for n, row in enumerate(rows, 1):
        if not any(c.strip() for c in row):
            blanks += 1
            continue
        label = f'{path.name} 第 {n} 行'
        if len(row) != 7:
            raise InputError(f'{label}：应有 7 列')
        kind = row[2].strip().upper()
        if kind == 'BRANCH':
            if branch:
                raise InputError(f'{label}：重复 BRANCH 记录')
            branch = True
            continue
        if kind == 'ACC-NO':
            if account is not None or not row[1].strip().isdigit():
                raise InputError(f'{label}：重复或无效账户编号')
            account = row[1].strip()
            continue
        date = bank_date(row[1], label)
        amount = money(row[3], label)
        segment = row[0].strip()
        if not branch or not account or not segment.isdigit():
            raise InputError(f'{label}：缺少账户资料或无效分段编号')
        if kind == 'OPEN':
            if active is not None:
                raise InputError(f'{label}：上一段缺少 CLOSE，不能重复开始')
            if last_closing is not None and amount != last_closing:
                raise InputError(f'{label}：上一段期末与下一段期初余额不一致')
            if segments and date < segments[-1]['closing_date']:
                raise InputError(f'{label}：分段日期倒退')
            active = {'id': segment, 'opening': amount, 'net': ZERO, 'count': 0, 'date': date, 'last_date': date}
            if first_opening is None:
                first_opening = amount
            continue
        if active is None or segment != active['id']:
            raise InputError(f'{label}：交易或 CLOSE 没有对应的 OPEN 分段')
        if date < active['last_date']:
            raise InputError(f'{label}：分段内日期倒退')
        if kind == 'CLOSE':
            if active['opening'] + active['net'] != amount:
                raise InputError(f'{label}：分段 {segment} 的期初＋交易净额与期末余额不平')
            segments.append({'id': segment, 'opening': format(active['opening'], '.2f'),
                             'closing': format(amount, '.2f'), 'net': format(active['net'], '.2f'),
                             'count': active['count'], 'closing_date': date})
            last_closing = amount
            active = None
            continue
        if not re.fullmatch(r'[A-Z][A-Z0-9 _/-]{0,19}', kind) or not row[4].strip():
            raise InputError(f'{label}：无效交易类型或缺少说明')
        transactions.append(Transaction(path.name, n, account, date, amount, row[4], ''))
        active['net'] += amount
        active['count'] += 1
        active['last_date'] = date
        fee_control += money(row[6], f'{label} 费用控制列')
    if active is not None or first_opening is None or last_closing is None or not account or not branch:
        raise InputError(f'{path.name}：缺少完整的账户、OPEN 或 CLOSE 记录')
    for segment in segments:
        segment['closing_date'] = segment['closing_date'].strftime('%Y-%m-%d')
    return source_summary(path, account, first_opening, last_closing, transactions, digest, encoding,
                          blanks, '7列分段流水', segments=segments,
                          fee_control_total=format(fee_control, '.2f'),
                          fee_control_note='费用控制列不另加到交易金额；按原始交易净额核对余额。')


def parse_call_statement(path, rows, digest, encoding):
    account = None
    branch = False
    opening = closing = None
    opening_date = closing_date = None
    transactions = []
    blanks = 0
    for n, row in enumerate(rows, 1):
        if not any(c.strip() for c in row):
            blanks += 1
            continue
        label = f'{path.name} 第 {n} 行'
        if len(row) != 8:
            raise InputError(f'{label}：应有 8 列')
        kind = row[2].strip().upper()
        if kind == 'BRANCH':
            if branch:
                raise InputError(f'{label}：重复 BRANCH')
            branch = True
            continue
        if kind == 'ACC-NO':
            if account is not None or not row[1].strip().isdigit():
                raise InputError(f'{label}：重复或无效账户编号')
            account = row[1].strip()
            continue
        if row[0].strip() != 'HIST' or not branch or not account:
            raise InputError(f'{label}：无法识别的存款流水记录')
        date = bank_date(row[1], label)
        amount = money(row[3], label)
        narrative = row[5].strip().upper()
        if narrative == 'STATEMENT OPENING BALANCE':
            if opening is not None or closing is not None or transactions:
                raise InputError(f'{label}：重复或错位的期初余额')
            opening, opening_date = amount, date
        elif narrative == 'STATEMENT CLOSING BALANCE':
            if opening is None or closing is not None:
                raise InputError(f'{label}：缺少期初或重复期末余额')
            closing, closing_date = amount, date
        else:
            if opening is None or closing is not None or not (row[4].strip() or row[5].strip()):
                raise InputError(f'{label}：交易位置或说明无效')
            # Keep the source's two narrative fields in their original output columns.
            transactions.append(Transaction(path.name, n, account, date, amount, row[4], row[5]))
    if opening is None or closing is None or not account or not branch:
        raise InputError(f'{path.name}：缺少存款流水的账户、期初或期末余额')
    if closing_date < opening_date or any(not opening_date <= t.date <= closing_date for t in transactions):
        raise InputError(f'{path.name}：交易日期超出期初及期末日期范围')
    return source_summary(path, account, opening, closing, transactions, digest, encoding,
                          blanks, '8列存款流水（HIST余额行）')


def parse_hist_statement(path: Path, prepared_data=None):
    rows, digest, encoding = prepared_data if prepared_data is not None else read_csv(path)
    metadata = {}
    pending = []
    ignored_blank_rows = 0
    for row_number, row in enumerate(rows, 1):
        if not any(cell.strip() for cell in row):
            ignored_blank_rows += 1
            continue
        label = f'{path.name} 第 {row_number} 行'
        if len(row) != 8:
            raise InputError(f'{label}：应有 8 列，实际 {len(row)} 列；银行格式可能已变化')
        marker = row[0].strip()
        if marker == 'HIST':
            if not re.fullmatch(r'\d{8}', row[1].strip()):
                raise InputError(f'{label}：日期必须是 YYYYMMDD')
            try:
                date = datetime.strptime(row[1].strip(), '%Y%m%d')
            except ValueError as exc:
                raise InputError(f'{label}：无效日期 {row[1]!r}') from exc
            if not row[4].strip():
                raise InputError(f'{label}：缺少交易说明')
            pending.append((row_number, date, money(row[3], label), row[4], row[5]))
            continue
        kind = row[2].strip()
        if kind not in ('BRANCH', 'ACC-NO', 'OPEN', 'CLOSE'):
            raise InputError(f'{label}：无法识别的记录类型 {marker!r}/{kind!r}，未生成导入文件')
        if kind in metadata:
            raise InputError(f'{label}：重复的 {kind} 记录')
        metadata[kind] = row
    for kind in ('BRANCH', 'ACC-NO', 'OPEN', 'CLOSE'):
        if kind not in metadata:
            raise InputError(f'{path.name}：缺少 {kind} 记录')
    account = metadata['ACC-NO'][1].strip()
    if not account or not account.isdigit():
        raise InputError(f'{path.name}：无效账户编号')
    opening = money(metadata['OPEN'][3], f'{path.name} 期初')
    closing = money(metadata['CLOSE'][3], f'{path.name} 期末')
    transactions = [Transaction(path.name, n, account, d, a, desc, ref)
                    for n, d, a, desc, ref in pending]
    net = sum((t.amount for t in transactions), ZERO)
    difference = opening + net - closing
    if difference != ZERO:
        raise InputError(f'{path.name}：期初＋交易净额－期末＝{difference:.2f}，余额不平，未生成导入文件')
    return transactions, {
        'file': path.name, 'account': account, 'count': len(transactions),
        'opening': format(opening, '.2f'), 'closing': format(closing, '.2f'),
        'net': format(net, '.2f'), 'balance_difference': format(difference, '.2f'),
        'sha256': digest, 'encoding': encoding, 'ignored_blank_rows': ignored_blank_rows,
    }


def compare_reference(path: Path, transactions):
    rows, digest, _ = read_csv(path)
    if not rows or rows[0][:7] != HEADER:
        raise InputError('对照成品的表头与预期的 7 列不符')
    reference_keys = []
    reference_row_numbers = []
    ignored = []
    other_values = []
    extra_column_rows = []
    if any(c.strip() for c in rows[0][7:]):
        extra_column_rows.append(1)
    for n, row in enumerate(rows[1:], 2):
        if not any(c.strip() for c in row):
            ignored.append({'row': n, 'reason': '空行'})
            continue
        if len(row) < 7:
            raise InputError(f'对照成品第 {n} 行：少于 7 列')
        if any(c.strip() for c in row[7:]):
            extra_column_rows.append(n)
        row = row[:7]
        if not row[2].strip():
            if any(c.strip() for c in row[1:]):
                raise InputError(f'对照成品第 {n} 行：有内容但缺少金额，无法比较')
            ignored.append({'row': n, 'reason': '只有日期，没有交易内容'})
            continue
        try:
            date = datetime.strptime(row[0], '%d/%m/%Y')
        except ValueError as exc:
            raise InputError(f'对照成品第 {n} 行：日期必须是 DD/MM/YYYY') from exc
        reference_keys.append((date.strftime('%Y-%m-%d'), money(row[2], f'成品第 {n} 行'), row[3].rstrip(), row[4].rstrip()))
        reference_row_numbers.append(n)
        if any(row[i].strip() for i in (1, 5, 6)):
            other_values.append(n)
    generated = [t.key() for t in transactions]
    missing = Counter(reference_keys) - Counter(generated)
    extra = Counter(generated) - Counter(reference_keys)
    differences = []
    for position, (source_key, reference_key) in enumerate(zip_longest(generated, reference_keys), 1):
        if source_key != reference_key:
            differences.append({'transaction_position': position,
                                'generated_erp_row': position + 2 if source_key is not None else None,
                                'reference_row': reference_row_numbers[position-1] if reference_key is not None else None,
                                'generated': [str(v) for v in source_key] if source_key else None,
                                'reference': [str(v) for v in reference_key] if reference_key else None})
    return {
        'file': path.name, 'sha256': digest, 'transaction_count': len(reference_keys),
        'same_transactions': not missing and not extra,
        'same_dates_and_amounts': Counter(k[:2] for k in generated) == Counter(k[:2] for k in reference_keys),
        'same_order': reference_keys == generated,
        'missing_in_generated': sum(missing.values()), 'extra_in_generated': sum(extra.values()),
        'nonempty_optional_columns_rows': other_values,
        'extra_columns_with_content_rows': extra_column_rows,
        'row_differences': differences,
        'excluded_reference_rows': ignored,
    }


def choose_sources(input_folder: Path | None = None, files=None, reference: Path | None = None):
    skipped = []
    if files is not None:
        paths = [Path(p).expanduser().resolve() for p in files]
        if not paths:
            raise InputError('请至少选择一份原始银行 CSV')
    else:
        if input_folder is None or not input_folder.expanduser().is_dir():
            raise InputError('请选择原始银行 CSV 文件，或有效的文件夹')
        folder = input_folder.expanduser().resolve()
        paths = []
        for path in folder.iterdir():
            if not path.is_file() or path.suffix.lower() not in bank_formats.ALLOWED:
                continue
            first = []
            if path.suffix.lower() in ('.csv','.tsv'):
                rows, _, _ = read_csv(path)
                first = next((row for row in rows if any(c.strip() for c in row)), [])
            if (reference and path.resolve() == reference.expanduser().resolve()) or first[:7] == HEADER or first[:2] in (['ERP行号', '来源文件'],['交易位置','字段']):
                skipped.append(path.name)
                continue
            paths.append(path)
        if not paths:
            raise InputError('此文件夹没有支持的银行文件；成品和来源追踪文件不会作为输入')
    if len(set(paths)) != len(paths):
        raise InputError('同一份文件被重复选择，请移除重复项')
    for path in paths:
        if not path.is_file():
            raise InputError(f'找不到原始文件：{path.name}')
        if path.suffix.lower() not in bank_formats.ALLOWED:
            raise InputError(f'{path.name}：支持 CSV、TSV、TXT、XLS、XLSX、OFX/QFX、camt XML 和 MT940；暂不支持 PDF 或图片')
    priority = {name.casefold(): n for n, name in enumerate(SOURCES)}
    paths.sort(key=lambda p: (priority.get(p.name.casefold(), len(priority)), p.name.casefold(), str(p)))
    return paths, sorted(skipped)


def prepare(input_folder: Path | None = None, period: str = '', reference: Path | None = None, *, files=None, date_order='DMY', profiles=None):
    paths, skipped = choose_sources(input_folder, files, reference)
    transactions, summaries = [], []
    for path in paths:
        tx, summary = parse_source(path, profiles=profiles, date_order=date_order)
        summary['path'] = str(path)
        transactions.extend(tx)
        summaries.append(summary)
    accounts = [s['account'] for s in summaries]
    if len(set(accounts)) != len(accounts):
        raise InputError('所选文件中有重复账户，请核实是否选入同一账户的重复导出文件')
    if len({s['sha256'] for s in summaries}) != len(summaries):
        raise InputError('所选文件有内容完全相同的重复导出，即使文件名不同也不能重复合并')
    currencies = {s.get('currency') for s in summaries if s.get('currency')}
    if len(currencies)>1:
        raise InputError('所选流水含多个币种，不能直接合并到同一个 IQ 金额列：'+'、'.join(sorted(currencies)))
    months = sorted({t.date.strftime('%Y-%m') for t in transactions})
    if not months:
        raise InputError('所选银行文件均无交易，未生成导入文件')
    period = period.strip()
    if not period:
        if len(months) != 1:
            raise InputError(f'交易涉及多个月份：{", ".join(months)}；请分别处理各月银行流水')
        period = months[0]
    if not re.fullmatch(r'\d{4}-\d{2}', period):
        raise InputError('月份格式无效：请留空自动识别，或填写 YYYY-MM（例如 2026-09），不能只填 8 或 9')
    try:
        datetime.strptime(period + '-01', '%Y-%m-%d')
    except ValueError as exc:
        raise InputError('月份无效') from exc
    if months != [period]:
        raise InputError(f'交易月份 {", ".join(months)} 与指定月份 {period} 不一致；不会静默筛掉交易')
    counts = Counter(t.key() for t in transactions)
    duplicates = [key for key, count in counts.items() if count > 1]
    warnings = [f"{s['file']}：{note}" for s in summaries for note in s.get('validation_notes',[])]
    if duplicates:
        warnings.append(f'{len(duplicates)} 组日期、金额、说明、参考号完全相同的交易；已保留所有记录，未自动去重。')
    zero_count = sum(t.amount == ZERO for t in transactions)
    if zero_count:
        warnings.append(f'原始银行流水中有 {zero_count} 笔真实零金额记录，已保留。')
    comparison = compare_reference(reference, transactions) if reference else None
    if comparison and not comparison['same_transactions']:
        if comparison['same_dates_and_amounts']:
            warnings.append('自动结果与成品的交易日期及金额一致，但说明或参考号不同；已按银行原始内容生成，字段差异见成品差异.csv。')
        else:
            warnings.append('自动结果与对照成品存在交易日期、金额或内容差异，请先查看校验报告。')
    if comparison and comparison['nonempty_optional_columns_rows']:
        warnings.append('对照成品的 SERVICE FEE、Balance 或 CHEQUE NUMBER 有值；当前规则不填这些列，需要核实。')
    if comparison and comparison.get('extra_columns_with_content_rows'):
        warnings.append('对照成品第 8 列及之后有额外内容；仅比较前 7 列，不把旁边的余额备注导入 IQ。')
    net = sum((t.amount for t in transactions), ZERO)
    report = {'period': period, 'transaction_count': len(transactions),
              'total_in': format(sum((t.amount for t in transactions if t.amount > 0), ZERO), '.2f'),
              'total_out': format(sum((-t.amount for t in transactions if t.amount < 0), ZERO), '.2f'),
              'net': format(net, '.2f'), 'source_count': len(summaries), 'sources': summaries,
              'skipped_csv_files': skipped,
              'warnings': warnings, 'reference_comparison': comparison}
    return transactions, report


def inspect_sources(input_folder=None, *, files=None, profiles=None, date_order='DMY'):
    paths, skipped = choose_sources(input_folder, files)
    checks = []
    for path in paths:
        try:
            tx, summary = parse_source(path, profiles=profiles, date_order=date_order)
            checks.append({'file':str(path),'ok':True,'format':summary.get('format','8列 HIST 流水'),
                           'count':len(tx),'months':sorted({t.date.strftime('%Y-%m') for t in tx}),
                           'validation':summary.get('validation','期初期末余额核平'),
                           'notes':summary.get('validation_notes',[])})
        except (InputError,OSError,ValueError) as exc:
            checks.append({'file':str(path),'ok':False,'error':str(exc)})
    return checks, skipped


def write_csv(path, rows, encoding='utf-8'):
    with Path(path).open('w', newline='', encoding=encoding) as handle:
        csv.writer(handle, lineterminator='\r\n').writerows(rows)


def import_rows(transactions, period):
    first = datetime.strptime(period + '-01', '%Y-%m-%d')
    last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
    opening = [first.strftime('%d/%m/%Y')] + [''] * 6
    closing = [last.strftime('%d/%m/%Y')] + [''] * 6
    return [HEADER, opening] + [t.output_row() for t in transactions] + [closing]


def write_result(transactions, report, output_root: Path):
    output_root = output_root.expanduser().resolve()
    run_name = report['period'] + '_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    destination = output_root / run_name
    destination.mkdir(parents=True, exist_ok=False)
    import_name = f"ERP_import_{report['period']}.csv"
    expected = import_rows(transactions, report['period'])
    report = dict(report, month_boundary_rows={'first': expected[1][0], 'last': expected[-1][0],
                                              'other_columns': 'blank', 'counted_as_transactions': False})
    write_csv(destination / import_name, expected)
    audit_rows = [['ERP行号', '来源文件', '原始行号', '账户编号', '日期', '金额', '说明', '参考号']]
    for n, t in enumerate(transactions, 3):
        audit_rows.append([n, t.source, t.source_row, t.account, t.date.strftime('%Y-%m-%d'),
                           format(t.amount, '.2f'), t.description, t.reference])
    write_csv(destination / '来源追踪.csv', audit_rows, 'utf-8-sig')
    (destination / '校验数据.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = [f"银行 CSV 合并校验报告　{report['period']}", '',
             f"流水文件数：{len(report['sources'])}", f"交易笔数：{report['transaction_count']}", f"流入合计：{report['total_in']}",
             f"流出合计：{report['total_out']}", f"净变动：{report['net']}", '',
             '来源文件 / 笔数 / 期初 / 净变动 / 期末 / 余额差额']
    for s in report['sources']:
        lines.append(f"{s['file']} / {s['count']} / {s['opening']} / {s['net']} / {s['closing']} / {s['balance_difference']}")
    fully_balanced = all(s['balance_difference']=='0.00' for s in report['sources'])
    lines += ['', '余额差额 = 期初 + 本期交易净额 - 期末。' + ('本次各账户均已核平。' if fully_balanced else '部分文件缺少独立余额，未完成完整核平，请查看各文件状态及提示。'),
              '只合并真实交易，不导入账户元数据、期初期末余额及空行；HIST 标记的余额行同样排除。',
              f"IQ 要求的顺序：表头 → 月初日期 {expected[1][0]} → 所有交易 → 月末日期 {expected[-1][0]}。",
              '月初和月末各一行，除日期外其余六列留空；这两行不计入交易笔数和金额合计。',
              '文件顺序：' + ' → '.join(s['file'] for s in report['sources']) + '；各文件内保持原序。',
              '日期：DD/MM/YYYY；金额为精确两位小数；说明及参考号原样保留。',
              'SERVICE FEE、Balance、CHEQUE NUMBER 按成品格式留空。',
              '内部转账两边全部保留，不相互抵消，不自动去重。',
              '本工具不执行 ERP 导入，也不修改原始文件。']
    for source in report['sources']:
        if source.get('validation'):
            lines.append(f"余额状态：{source['file']} — {source['validation']}")
        if source.get('format'):
            lines.append(f"识别格式：{source['file']} — {source['format']}")
        if source.get('segments'):
            lines.append(f"分段校验：{source['file']} 共 {len(source['segments'])} 段，各段余额及前后衔接均核平。")
            lines.append('费用控制列合计 ' + source['fee_control_total'] + '，不重复叠加到交易金额。')
    if report.get('skipped_csv_files'):
        lines += ['', '文件夹中已排除的成品或来源追踪 CSV：' + '、'.join(report['skipped_csv_files'])]
    comparison = report['reference_comparison']
    if comparison:
        lines += ['', f"对照成品：{comparison['file']}",
                  f"对照交易数：{comparison['transaction_count']}",
                  f"交易完全一致：{'是' if comparison['same_transactions'] else '否'}",
                  f"仅按日期及金额核对一致：{'是' if comparison['same_dates_and_amounts'] else '否'}",
                  f"交易顺序一致：{'是' if comparison['same_order'] else '否'}",
                  f"按日期、金额、说明、参考号四字段比较：成品中无法匹配 {comparison['missing_in_generated']} 条；自动结果中无法匹配 {comparison['extra_in_generated']} 条。字段差异也会导致无法匹配，不等同于遗漏交易。",
                  '对照成品中以下非交易行不参与逐笔交易比较；输出的月初和月末日期行按所选月份重新生成：']
        lines.extend(f"第 {r['row']} 行：{r['reason']}" for r in comparison['excluded_reference_rows'])
        if comparison.get('row_differences'):
            lines += ['按交易位置比较的字段差异详见“成品差异.csv”；以原始银行记录生成，不复制成品中不对应的内容。']
            difference_rows = [['交易位置', '字段', '银行原始记录生成值', '人工成品值', '自动文件行号', '人工成品行号']]
            for difference in comparison['row_differences']:
                generated = difference['generated'] or ['不存在'] * 4
                reference_values = difference['reference'] or ['不存在'] * 4
                for i, field in enumerate(('Date', 'Amount', 'DESCRIPTION', 'REFERENCE')):
                    if generated[i] != reference_values[i]:
                        difference_rows.append([difference['transaction_position'], field, generated[i], reference_values[i],
                                                difference['generated_erp_row'], difference['reference_row']])
            write_csv(destination / '成品差异.csv', difference_rows, 'utf-8-sig')
    lines += ['', '提示：'] + (report['warnings'] or ['无。'])
    (destination / '校验报告.txt').write_text('\n'.join(lines), encoding='utf-8-sig')
    # Re-read the on-disk import so serialization errors cannot pass unnoticed.
    actual, _, _ = read_csv(destination / import_name)
    if actual != expected:
        raise RuntimeError('输出 CSV 复读校验失败，请勿使用此目录')
    return destination


def gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title('银行流水合并工具 v5.1')
    root.geometry('1020x730')
    root.minsize(950, 650)
    frame = ttk.Frame(root, padding=22)
    frame.pack(fill='both', expand=True)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(10, weight=1)
    ttk.Label(frame, text='银行流水合并工具', font=(UI_FONT, 18, 'bold')).grid(row=0, column=0, sticky='w', pady=(0, 8))
    ttk.Label(frame, text='CSV / Excel / OFX / MT940 / camt XML；先检查格式，再生成 IQ 月初 → 交易 → 月末', font=(UI_FONT, 10)).grid(row=1, column=0, sticky='w', pady=(0, 18))
    source_display = tk.StringVar(value='尚未选择银行流水')
    selection = {'files': None, 'folder': None}
    reference = tk.StringVar()
    period = tk.StringVar()
    date_order = tk.StringVar(value='DMY')
    output = tk.StringVar(value=str(Path(__file__).resolve().parent / 'results'))
    destination = {'path': None}

    def path_field(row, label, variable, picker):
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky='w', pady=(8, 4))
        line = ttk.Frame(frame)
        line.grid(row=row+1, column=0, sticky='ew')
        line.columnconfigure(0, weight=1)
        ttk.Entry(line, textvariable=variable).grid(row=0, column=0, sticky='ew', padx=(0, 8))
        ttk.Button(line, text='选择…', command=picker).grid(row=0, column=1)

    def pick_folder(variable):
        selected = filedialog.askdirectory(parent=root)
        if selected:
            variable.set(selected)

    def pick_reference():
        selected = filedialog.askopenfilename(parent=root, filetypes=[('CSV', '*.csv')])
        if selected:
            reference.set(selected)

    def pick_files():
        chosen = filedialog.askopenfilenames(parent=root, title='选择原始银行流水（可按 Ctrl 或 Shift 多选）', filetypes=[('银行文件', '*.csv *.tsv *.txt *.xlsx *.xls *.ofx *.qfx *.xml *.mt940 *.sta'),('所有文件','*.*')])
        if chosen:
            selection['files'] = [Path(p) for p in chosen]
            selection['folder'] = None
            source_display.set(f'已选择 {len(chosen)} 份：' + '、'.join(Path(p).name for p in chosen))
            status.set(f'已选择 {len(chosen)} 份流水。月份可留空自动识别。')
            display('已选择的原始银行流水：\n' + '\n'.join(chosen))
            root.after(50,check_formats)

    def pick_input_folder():
        chosen = filedialog.askdirectory(parent=root, title='选择银行 CSV 所在文件夹')
        if chosen:
            selection['files'] = None
            selection['folder'] = Path(chosen)
            source_display.set(chosen)
            status.set('已选择文件夹。自动读取其中的银行 CSV，排除已知格式的成品及来源追踪文件。')
            display('原始文件夹：\n' + chosen)
            root.after(50,check_formats)

    ttk.Label(frame, text='原始银行流水（可多选，选择后自动检查格式）').grid(row=2, column=0, sticky='w', pady=(8, 4))
    source_line = ttk.Frame(frame)
    source_line.grid(row=3, column=0, sticky='ew')
    source_line.columnconfigure(0, weight=1)
    ttk.Entry(source_line, textvariable=source_display, state='readonly').grid(row=0, column=0, sticky='ew', padx=(0, 8))
    ttk.Button(source_line, text='选择流水…', command=pick_files).grid(row=0, column=1, padx=(0, 6))
    ttk.Button(source_line, text='选择文件夹…', command=pick_input_folder).grid(row=0, column=2)
    path_field(4, '对照成品（可选；留空也能生成）', reference, pick_reference)
    path_field(6, '结果保存位置（每次新建子文件夹，不覆盖已有结果）', output, lambda: pick_folder(output))
    controls = ttk.Frame(frame)
    controls.grid(row=8, column=0, sticky='ew', pady=(16, 10))
    ttk.Label(controls, text='月份').pack(side='left')
    ttk.Entry(controls, textvariable=period, width=12).pack(side='left', padx=8)
    ttk.Label(controls, text='留空自动识别，或填写 YYYY-MM').pack(side='left')
    ttk.Label(controls,text='  日期顺序').pack(side='left')
    ttk.Combobox(controls,textvariable=date_order,values=('DMY','MDY','AUTO'),state='readonly',width=6).pack(side='left',padx=5)
    status = tk.StringVar(value='点击“选择流水…”可多选 CSV；也可选择文件夹。不会修改原文件或操作 ERP。')
    ttk.Label(frame, textvariable=status, wraplength=740).grid(row=9, column=0, sticky='w', pady=6)
    log = tk.Text(frame, height=11, wrap='word', font=(UI_FONT, 10), state='disabled')
    log.grid(row=10, column=0, sticky='nsew', pady=(6, 0))

    def display(text):
        log.configure(state='normal')
        log.delete('1.0', 'end')
        log.insert('end', text)
        log.configure(state='disabled')

    def generate():
        if (selection['files'] is None and selection['folder'] is None) or not output.get().strip():
            messagebox.showerror('缺少路径', '请选择原始银行流水和结果保存位置。', parent=root)
            return
        try:
            tx, report = prepare(selection['folder'], period.get(), Path(reference.get()) if reference.get().strip() else None, files=selection['files'], date_order=date_order.get())
            path = write_result(tx, report, Path(output.get()))
            destination['path'] = path
            status.set(f"已合并 {len(report['sources'])} 份流水、{report['transaction_count']} 笔交易；{'有提示，请查看报告' if report['warnings'] else '各账户余额均核平'}。")
            display((path / '校验报告.txt').read_text(encoding='utf-8-sig') + '\n\n结果位置：\n' + str(path))
        except (InputError, OSError, csv.Error, RuntimeError) as exc:
            destination['path'] = None
            status.set('生成未完成，请检查输入。')
            display(str(exc))
            messagebox.showerror('未生成可用结果', str(exc), parent=root)

    def open_result():
        if destination['path']:
            if sys.platform == 'win32': os.startfile(destination['path'])
            else: subprocess.Popen(['open' if sys.platform=='darwin' else 'xdg-open',str(destination['path'])])

    def check_formats():
        try:
            checks,skipped=inspect_sources(selection['folder'],files=selection['files'],date_order=date_order.get())
            lines=['格式预检查（不会写入导入文件）', '日期顺序：'+date_order.get()+'；DMY=日/月/年，MDY=月/日/年，AUTO=有歧义则提示。','']
            for item in checks:
                lines.append(Path(item['file']).name)
                if item['ok']:
                    lines.append(f"  已识别：{item['format']}；{item['count']} 笔；月份 {', '.join(item['months'])}")
                    lines.append('  '+item['validation'])
                    lines.extend('  提示：'+n for n in item['notes'])
                else:lines.append('  需要处理：'+item['error'])
            if skipped:lines.append('已排除成品/核查文件：'+ '、'.join(skipped))
            status.set(f"格式检查：{sum(i['ok'] for i in checks)}/{len(checks)} 份可解析；不认识的表格可用“设置列对应”。")
            display('\n'.join(lines))
        except (InputError,OSError,ValueError) as exc:
            status.set('请选择文件后再检查。');display(str(exc))

    def map_columns():
        try:
            paths,_=choose_sources(selection['folder'],selection['files'])
            from bank_mapping_ui import mapping_dialog
            mapping_dialog(root,paths,RULES_PATH,check_formats)
        except (InputError,OSError,ValueError) as exc:messagebox.showerror('无法设置列对应',str(exc),parent=root)

    extras=ttk.Frame(frame)
    extras.grid(row=11,column=0,sticky='ew',pady=(10,0))
    ttk.Button(extras,text='检查格式',command=check_formats).pack(side='left')
    ttk.Button(extras,text='设置列对应…',command=map_columns).pack(side='left',padx=8)
    ttk.Label(extras,text='不认识的表头可以保存为自己的规则，之后同样布局自动复用。').pack(side='left',padx=8)

    ttk.Button(controls, text='打开结果', command=open_result).pack(side='right')
    ttk.Button(controls, text='校验并生成', command=generate).pack(side='right', padx=8)
    root.mainloop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument('--input', type=Path, help='原始 CSV 文件夹，自动识别，不限制数量和名称')
    inputs.add_argument('--files', type=Path, nargs='+', help='一份或多份原始银行 CSV')
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent / 'results')
    parser.add_argument('--period', default='', help='YYYY-MM，省略则从交易自动识别')
    parser.add_argument('--reference', type=Path, help='可选的人工成品，用于逐笔对照')
    parser.add_argument('--date-order',choices=('DMY','MDY','AUTO'),default='DMY')
    parser.add_argument('--check',action='store_true',help='仅检查每份文件的格式及校验状态，不生成结果')
    args = parser.parse_args()
    if args.input is None and args.files is None:
        gui()
        return
    if args.check:
        checks,skipped=inspect_sources(args.input,files=args.files,date_order=args.date_order)
        print(json.dumps({'checks':checks,'skipped':skipped},ensure_ascii=False,indent=2))
        return
    tx, report = prepare(args.input, args.period, args.reference, files=args.files,date_order=args.date_order)
    destination = write_result(tx, report, args.output)
    print(json.dumps({'output': str(destination), 'count': report['transaction_count'],
                      'net': report['net'], 'warnings': report['warnings']}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (InputError, OSError, csv.Error) as error:
        raise SystemExit(str(error))
