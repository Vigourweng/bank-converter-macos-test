# Bank statement converter v5.3

This local tool combines supported statement files into an IQ import CSV. This public repository contains application code and fictional test data. **Keep real statements, customer information, personal paths, mapping rules and generated results outside this repository.**

## Start on Windows

Install Python 3.10 or newer with tkinter, extract the complete tool folder, and open `start.cmd`. Keep the modules and `vendor` folder together when copying or moving the tool.

## Start on Mac

Use **`start_mac.pyw`** with the official PythonLauncher. The included **`Mac使用说明.html`** is a Chinese guide that opens offline.

1. Install the official [Python 3.13.17 Mac installer](https://www.python.org/ftp/python/3.13.17/python-3.13.17-macos11.pkg), the version used in the launch check below. It includes PythonLauncher and tkinter. Extract the entire tool folder and keep all files together.
2. In Finder, open **Applications → Python 3.13 → PythonLauncher** itself, without opening a script. Its name may appear as **Python Launcher**. Open **Preferences**, select the **Python Script** file type, and uncheck **Run in a Terminal window** (the checkbox mentioning Terminal). Close Preferences and quit PythonLauncher to save the setting. This normal launch preference also affects other files of that type opened by PythonLauncher.
3. Right-click **`start_mac.pyw` → Open With → PythonLauncher**. If absent from the list, use **Other…** and find it in **Applications → Python 3.13**. If a reminder about the file type/default application appears, click **OK**. In the tested cases this was an ordinary PythonLauncher reminder, and the bank tool window appeared.
4. Open `start_mac.pyw` the same way next time. To remember the app choice, select the file in Finder and choose **Get Info → Open with → PythonLauncher**.

The older `start_mac.command` remains included, but some downloaded copies can still be blocked. The recommended `.pyw` entry point opens the same conversion interface and preserves the supported formats and IQ output rules.

## Validation status

[Real Mac launch run 40195756629](https://github.com/Vigourweng/bank-converter-macos-test/actions/runs/40195756629) displayed the bank tool window in all three cases: no quarantine attribute, synthetic quarantine value `0083`, and synthetic quarantine value `0087`. The normal **Python Script / Run in a Terminal window** preference was turned off. A file-type binding reminder appeared; the “damaged” warning did not appear in those cases.

With the default Terminal preference, [run 40195406500](https://github.com/Vigourweng/bank-converter-macos-test/actions/runs/40195406500) stopped at the macOS permission prompt for controlling Terminal. The instructions above use PythonLauncher's normal window-launch preference.

These are synthetic launch checks, **not a test of the user's actual Lark or browser download**. Every Mac/download combination is not guaranteed. Earlier [Intel and Apple Silicon macOS 15 checks](https://github.com/Vigourweng/bank-converter-macos-test/actions/runs/40187642632) passed the corresponding release's conversion, real Tk windows, Generate and mapping-save workflows. New releases need their own packaged checks.

## Using the tool and supported formats

Selecting statements starts a format check. Unrecognized table layouts can be configured in the column-mapping window and saved locally in `format_rules.json`. This package starts with empty rules.

The **支持范围** button opens the included offline `支持范围.txt` with format families, bank/channel status, known gaps and official references. Supported families include implemented bank CSV variants, common headed tables, Excel, OFX/QFX, QIF Bank, OFC, MT940, camt.053 and the documented FNB/RMB Stmnt3 RSA/CUR/version3 ASCII subset. This is format coverage, not certification of every South African bank export. PDFs, images, damaged/encrypted workbooks and unimplemented bank-specific variants are unsupported.

QIF's separate date setting starts at **AUTO**, independently of table dates. Ambiguous QIF dates stop processing until the source's order is selected explicitly. FNB's published QIF specification uses **MDY**. Use the selector or `--qif-date-order MDY` only after checking the source. Missing opening/closing balances are reported; the tool does not claim a full reconciliation without them.

## IQ output contract

The import file has seven columns: `Date`, `SERVICE FEE`, `Amount`, `DESCRIPTION`, `REFERENCE`, `Balance`, and `CHEQUE NUMBER`.

Its order is **header → first calendar day of the month → all transactions → last calendar day of the month**. The other six fields in both boundary rows are blank. Boundary rows do not count as transactions or affect totals. Amount signs, internal transfers and suspected duplicates are preserved. Sources are processed in alphabetical filename order, preserving each file's row order.

Command-line use remains available: `python3 bank_merge.py --files path/to/statement.csv --output results`.

## Building and third-party software

Run `python3 build_release.py` to create `dist/bank-converter.zip`. Only allowlisted application files and the vendored XLS reader are packaged. Statements, results, caches and CI logs are excluded. The older Mac command file's executable metadata is preserved.

The XLS reader is xlrd 2.0.2. Its notices and full license are retained in `vendor/LICENSE.xlrd`. This product includes software developed by David Giffin <david@giffin.org>. These are upstream attribution details. No project-wide redistribution license has been selected for the application code.

## 中文说明

这是银行流水合并工具 **v5.3** 的公开测试仓库，只使用程序和虚构测试数据。真实流水、客户资料、个人路径、自定义规则和生成结果应留在本地。

### Mac 第一次使用

推荐用 **`start_mac.pyw`** 启动。文件夹中的 **`Mac使用说明.html`** 可以离线打开。

1. 安装官方 [Python 3.13.17 Mac 安装包](https://www.python.org/ftp/python/3.13.17/python-3.13.17-macos11.pkg)，这是本次启动实测所用版本，包含 PythonLauncher 和 tkinter。解压完整工具文件夹，保留所有文件，不能只复制启动文件。
2. 在 Finder 中打开 **应用程序 → Python 3.13 → PythonLauncher** 本身，先不要打开脚本。名称也可能显示为 **Python Launcher**。打开 **Preferences（偏好设置）**，文件类型选 **Python Script**，取消勾选 **Terminal 窗口选项（Run in a Terminal window）**。关闭偏好设置，再退出 PythonLauncher 保存设置。这个普通设置也会影响它打开的其他同类脚本。
3. 右键 **`start_mac.pyw` → 打开方式 → PythonLauncher**。若没有列出，选 **其他…**，到 **应用程序 → Python 3.13** 中选择。若出现文件类型或默认打开程序的提醒，点 **OK**；本次实测中这是普通提醒，之后显示银行工具窗口。
4. 以后用相同方式打开。也可在 Finder 选中文件，打开 **显示简介 → 打开方式 → PythonLauncher**，记住打开程序。

旧的 `start_mac.command` 仍保留，但某些下载副本可能继续被阻止；请使用上面的 `.pyw` 入口。v5.3 只增加启动方式和说明，流水解析及 IQ 规则保持一致。

### 实测结果与限制

[Mac 原生启动实测 40195756629](https://github.com/Vigourweng/bank-converter-macos-test/actions/runs/40195756629) 在无隔离属性、人工设置 `0083`、人工设置 `0087` 三种情况下，都显示了银行工具窗口。测试关闭了 PythonLauncher 的 Terminal 启动选项；出现普通文件类型提醒，没有出现“已损坏”提示。

[默认设置实验 40195406500](https://github.com/Vigourweng/bank-converter-macos-test/actions/runs/40195406500) 停在 macOS 请求控制 Terminal 的权限提示，因此采用上述普通偏好设置启动。

这些结果**不是对你实际从 Lark 或浏览器下载的文件进行的测试**，不能保证所有 Mac 和下载副本都相同。此前 Intel 与 Apple Silicon 的 macOS 15 测试验证了对应版本的合并、生成和列规则保存；新版本仍需检查打包结果。

Windows 安装 Python 3.10 或以上及 tkinter 后使用 `start.cmd`。复制、移动时保留完整文件夹。

### 流水与 IQ 导入

选择原始流水后会自动检查格式，陌生表头可用“设置列对应”保存规则。“支持范围”按钮可离线查看格式和限制；已实现的格式不代表所有银行导出都已认证。QIF 单独使用 AUTO/MDY/DMY，遇到歧义会停止；FNB 官方 QIF 使用 MDY，需核对来源后再选择。

IQ 顺序固定为 **表头 → 月初日期 → 所有交易 → 月末日期**。首尾日期行的其余六列留空，不计入笔数和金额。金额正负号、内部转账和疑似重复交易均保留；按文件名字母顺序处理，文件内保留原始行序。缺少完整期初期末余额时会明确提示，不会声称已经完整核平。
