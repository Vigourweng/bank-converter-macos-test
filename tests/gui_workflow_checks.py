"""Exercise the real Tk callbacks using only generated, disposable statements.

The native file picker is replaced with a deterministic selection. Application
Generate, mapping validation/save, profile loading, and CSV writing stay real.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import sys
import traceback


def workflow_child(args, app, screenshot_window, *, require_macos=True):
    """Run inside the harness's bounded GUI child process and return evidence."""
    if require_macos and sys.platform != "darwin":
        raise RuntimeError("Workflow GUI check requires a real macOS process")
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    rules_path = Path(args.rules_output)
    work = rules_path.parent / "synthetic-gui-workflow"
    work.mkdir(parents=True, exist_ok=False)
    output = work / "generated outputs"
    standard = work / "Synthetic known headings.csv"
    custom = work / "Synthetic opaque layout.csv"
    reused = work / "Synthetic next month different filename.csv"
    standard.write_text(
        "Date,Description,Amount,Balance,Account Number\n"
        ",Opening balance,,1000,000789\n"
        "01/09/2026,SYNTHETIC RECEIPT,200,1200,000789\n"
        "02/09/2026,SYNTHETIC PAYMENT,-50,1150,000789\n"
        ",Closing balance,,1150,000789\n", encoding="utf-8",
    )
    custom.write_text(
        "Field A,Field B,Field C,Field D,Field E\n"
        ",Opening balance,,1000,000789\n"
        "01/09/2026,SYNTHETIC RECEIPT,200,1200,000789\n"
        "02/09/2026,SYNTHETIC PAYMENT,-50,1150,000789\n"
        ",Closing balance,,1150,000789\n", encoding="utf-8",
    )
    reused.write_text(
        "Field A,Field B,Field C,Field D,Field E\n"
        ",Opening balance,,1150,000789\n"
        "03/10/2026,SYNTHETIC NEXT RECEIPT,300,1450,000789\n"
        "04/10/2026,SYNTHETIC NEXT PAYMENT,-25,1425,000789\n"
        ",Closing balance,,1425,000789\n", encoding="utf-8",
    )
    sources = (standard, custom, reused)
    original_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    # Reject a fixture that has accidentally become auto-recognizable; otherwise
    # profile persistence/reuse would not actually be tested.
    assert not app.bank_formats.valid_columns(app.bank_formats.infer_columns(
        ["Field A", "Field B", "Field C", "Field D", "Field E"]
    )), "Opaque fixture no longer requires explicit mapping"

    result = {
        "kind": "workflow", "callback_errors": [], "dialogs": [],
        "checks": [], "native_file_picker": "deterministically substituted",
        "screenshots": {},
    }
    original_root = tk.Tk
    original_picker = filedialog.askopenfilenames
    original_error = messagebox.showerror
    original_info = messagebox.showinfo
    original_rules = app.RULES_PATH
    selected = [standard]
    state = {"root": None, "finished": False}

    def widgets(parent):
        for child in parent.winfo_children():
            yield child
            yield from widgets(child)

    def button(parent, text):
        matches = [w for w in widgets(parent)
                   if isinstance(w, ttk.Button) and w.cget("text") == text]
        assert len(matches) == 1, f"Expected one button {text!r}, found {len(matches)}"
        return matches[0]

    def log_text():
        matches = [w for w in widgets(state["root"]) if isinstance(w, tk.Text)]
        assert len(matches) == 1, "Expected the actual main-window report area"
        return matches[0].get("1.0", "end")

    def record_callback_error(exc_type, value, tb):
        result["callback_errors"].append("".join(traceback.format_exception(exc_type, value, tb)))
        root = state["root"]
        if root is not None:
            try:
                root.destroy()
            except tk.TclError:
                pass

    def error_dialog(title, message, **unused):
        result["dialogs"].append({"type": "error", "title": title, "message": str(message)})
        raise AssertionError(f"Unexpected application error dialog: {title}: {message}")

    def info_dialog(title, message, **unused):
        result["dialogs"].append({"type": "info", "title": title, "message": str(message)})
        return "ok"

    def optional_screenshot(window, name):
        if not args.screenshot:
            return
        target = Path(args.screenshot).with_name(Path(args.screenshot).stem + "-" + name + ".png")
        try:
            result["screenshots"][name] = screenshot_window(window, target)
        except Exception as error:
            result["screenshots"][name] = {"captured": False, "reason": str(error)}

    def check_generation(period, count, descriptions, net):
        # This inspects only the real Generate callback's disk output. It never
        # calls prepare/write_result directly or replaces their implementations.
        runs = sorted(output.glob("*/校验数据.json"))
        assert len(runs) == count, f"Expected {count} distinct generated result folders"
        current = runs[-1]
        report = json.loads(current.read_text(encoding="utf-8"))
        assert report["period"] == period
        assert report["transaction_count"] == 2 and report["source_count"] == 1
        assert report["net"] == net and not report["warnings"], report
        assert report["sources"][0]["balance_difference"] == "0.00", report
        import_path = current.parent / f"ERP_import_{period}.csv"
        with import_path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        assert len(rows) == 5 and all(len(row) == 7 for row in rows), rows
        assert rows[0] == app.HEADER, rows[0]
        first, last = (("01/09/2026", "30/09/2026") if period == "2026-09"
                       else ("01/10/2026", "31/10/2026"))
        assert rows[1] == [first] + [""] * 6, rows[1]
        assert rows[-1] == [last] + [""] * 6, rows[-1]
        assert [row[3] for row in rows[2:-1]] == descriptions, rows
        assert str(current.parent) in log_text(), "Report area did not show generated output"
        result["checks"].append({
            "action": "Generate", "period": period, "output": str(import_path),
            "transaction_count": 2, "net": net,
            "boundary_rows_blank_except_date": True,
        })

    def choose(path, callback):
        selected[0] = path
        button(state["root"], "选择流水…").invoke()
        # Selection deliberately schedules its real precheck after 50 ms.
        # Continue in a later event so that the application timer has run.
        state["root"].after(180, callback)

    def start():
        root = state["root"]
        default_output = str(Path(app.__file__).resolve().parent / "results")
        entries = [w for w in widgets(root) if isinstance(w, ttk.Entry)
                   and w.get() == default_output]
        assert len(entries) == 1, "Could not identify the real results-folder input"
        root.setvar(entries[0].cget("textvariable"), str(output))
        choose(standard, generate_standard)

    def generate_standard():
        button(state["root"], "校验并生成").invoke()
        check_generation("2026-09", 1, ["SYNTHETIC RECEIPT", "SYNTHETIC PAYMENT"], "150.00")
        choose(custom, open_mapping)

    def open_mapping():
        assert "需要处理" in log_text(), "Unknown layout was not flagged by the GUI precheck"
        button(state["root"], "设置列对应…").invoke()
        state["root"].after(120, save_mapping)

    def save_mapping():
        root = state["root"]
        windows = [child for child in root.winfo_children() if isinstance(child, tk.Toplevel)]
        assert len(windows) == 1, "Actual main-window map action did not open its dialog"
        window = windows[0]
        target_values = {"date": "1: Field A", "description": "2: Field B",
                         "amount": "3: Field C", "balance": "4: Field D", "account": "5: Field E"}
        for role, label in app.bank_formats.FIELDS.items():
            labels = [w for w in widgets(window) if isinstance(w, ttk.Label) and w.cget("text") == label]
            assert len(labels) == 1, f"Missing mapping field {role}"
            field_label = labels[0]
            grid = field_label.grid_info()
            peers = field_label.master.grid_slaves(row=grid["row"], column=int(grid["column"]) + 1)
            assert len(peers) == 1 and isinstance(peers[0], ttk.Combobox), role
            combo = peers[0]
            value = target_values.get(role, "不使用")
            assert value in combo["values"], (role, combo["values"])
            combo.set(value)
        optional_screenshot(window, "mapping-filled")
        button(window, "验证并保存规则").invoke()
        assert not window.winfo_exists(), "Save did not close mapping window"
        assert rules_path.is_file(), "Real map-save action did not write its profile"
        saved = json.loads(rules_path.read_text(encoding="utf-8"))
        assert len(saved["profiles"]) == 1, saved
        assert saved["profiles"][0]["columns"] == {
            "date": [0], "description": [1], "amount": [2], "balance": [3], "account": [4]
        }, saved
        assert any(dialog["type"] == "info" for dialog in result["dialogs"]), result
        assert "已识别" in log_text() and "需要处理" not in log_text(), "Save callback did not refresh the main precheck"
        result["checks"].append({"action": "map-save", "profile_count": 1,
                                  "saved_columns": saved["profiles"][0]["columns"]})
        button(root, "校验并生成").invoke()
        check_generation("2026-09", 2, ["SYNTHETIC RECEIPT", "SYNTHETIC PAYMENT"], "150.00")
        choose(reused, generate_reused)

    def generate_reused():
        assert "已识别" in log_text() and "需要处理" not in log_text(), "Persisted rule did not apply to new filename/month"
        button(state["root"], "校验并生成").invoke()
        check_generation("2026-10", 3, ["SYNTHETIC NEXT RECEIPT", "SYNTHETIC NEXT PAYMENT"], "275.00")
        assert original_hashes == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
        result["checks"].append({"action": "profile-reuse", "different_filename": True,
                                  "different_month": True, "sources_unchanged": True})
        optional_screenshot(state["root"], "generated")
        state["finished"] = True
        state["root"].destroy()

    def expired():
        raise AssertionError("Functional GUI workflow exceeded its 15-second watchdog")

    def instrumented_root(*positional, **keywords):
        root = original_root(*positional, **keywords)
        state["root"] = root
        root.report_callback_exception = record_callback_error
        root.after(400, start)
        root.after(15000, expired)
        return root

    tk.Tk = instrumented_root
    filedialog.askopenfilenames = lambda **unused: (str(selected[0]),)
    messagebox.showerror = error_dialog
    messagebox.showinfo = info_dialog
    app.RULES_PATH = rules_path
    try:
        app.gui()
    finally:
        tk.Tk = original_root
        filedialog.askopenfilenames = original_picker
        messagebox.showerror = original_error
        messagebox.showinfo = original_info
        app.RULES_PATH = original_rules
        root = state["root"]
        if root is not None:
            try:
                root.destroy()
            except tk.TclError:
                pass
    assert not result["callback_errors"], result["callback_errors"]
    assert state["finished"], "GUI workflow did not reach its completion assertion"
    return result
