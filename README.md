# Bank statement converter: public macOS test repository

This repository contains a local statement-conversion tool and synthetic test cases. It exists to test release packaging and macOS compatibility. It contains **no real bank statements, real account numbers, customer names, personal paths, or user-generated reports**. Do not upload production statements or application results here.

## Current validation status

The conversion core has been tested on Windows. macOS runtime, Finder launch, and the reported “damaged” warning are **not yet verified or resolved**. Check the repository's Actions page for actual run results. A green CI job establishes only the checks explicitly listed in its report; it does not prove that a Safari-downloaded, quarantined copy launches on every Mac.

## Output contract

The IQ import file has seven columns: `Date`, `SERVICE FEE`, `Amount`, `DESCRIPTION`, `REFERENCE`, `Balance`, and `CHEQUE NUMBER`.

Its row order is:

1. Column header.
2. First calendar day of the statement month, with the other six fields blank.
3. All selected transactions.
4. Last calendar day of the statement month, with the other six fields blank.

The two boundary rows are not transactions. They do not contribute to transaction counts or totals. Amount signs are preserved; internal transfers and suspected duplicates are retained. Sources are processed in alphabetical filename order, preserving each file's row order.

## Running locally

Use Python 3.10 or newer with tkinter. The official [Python macOS installer](https://www.python.org/downloads/macos/) includes Tcl/Tk. Extract or copy the complete folder, including the Python modules and `vendor` folder.

- Windows: run `start.cmd`.
- macOS: run `start_mac.command` from Finder, or invoke it with `sh` from a terminal. If macOS reports damage or blocks execution, preserve the exact warning and investigate the downloaded file; do not disable system protections as a workaround.
- Command line: `python3 bank_merge.py --files path/to/statement.csv --output results`.

Selecting files starts a format check. Unrecognized tabular layouts can be configured with the column-mapping window and saved in `format_rules.json`. This public copy starts with an empty rules file. Keep any personal mapping rules and real data outside this repository.

The program distinguishes independently balanced statements from transaction lists that lack opening/closing balances. It does not claim a full reconciliation when source balances are unavailable. Supported formats include the implemented bank CSV variants, common headed tables, Excel, OFX/QFX, MT940 and camt.053 subsets. It does not support every bank-specific variant, damaged or encrypted workbooks, PDFs or images.

## Building an allowlisted release

Run `python3 build_release.py`. The output is `dist/bank-converter.zip`. The builder packages only explicitly listed application files and the vendored XLS reader. It does not package local statements, reports, caches, CI logs, or arbitrary files. The macOS command file's executable metadata is preserved in the ZIP.

## Third-party software

The pure-Python XLS reader is xlrd 2.0.2. Its source copyright notices and complete license are retained in `vendor/LICENSE.xlrd`. This product includes software developed by David Giffin <david@giffin.org>. These are upstream public attribution details, not end-user contact information.

No project-wide redistribution license has been selected for the application code; the vendored component retains its own license.

## 中文说明

这是独立的公开测试仓库，只使用程序及虚构测试数据。不要上传真实银行流水、账号、客户资料、个人路径、生成结果或测试日志中的私人信息。

IQ 导入顺序固定为：**表头 → 月初日期行 → 所有交易 → 月末日期行**。首尾日期行的其余六列留空，不计入交易笔数和金额。按文件名字母顺序处理，每份文件内保留原始行序。

当前不能把 Windows 测试当作 Mac 实测。请查看 Actions 的实际报告；Finder 双击、浏览器下载隔离属性和“已损坏”弹窗需要单独验证，不能只凭 CI 绿色结果认定解决。

运行需要 Python 3.10 或以上及 tkinter。Windows 使用 `start.cmd`，Mac 使用 `start_mac.command`。复制、移动时保留完整文件夹。自定义列规则及真实输入应放在本地，不提交到这个公开仓库。
