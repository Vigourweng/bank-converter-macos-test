"""Check a release with synthetic data; only real macOS can pass Mac checks."""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import stat
import subprocess
import sys
import traceback
from zipfile import ZipFile


LIMITATIONS = [
    "A CI extraction is not a Safari-downloaded quarantined file. The affected Mac's Finder/Gatekeeper 'damaged' error still needs a separate check.",
    "GUI workflow checks invoke real application callbacks with synthetic inputs, while file-picker results and message boxes use test substitutes. Native file-dialog interaction and Finder opening still need a user check.",
    "Synthetic parser regressions cover the supported format families and important validation cases; they cannot cover every bank-specific layout or export variant.",
    "The original installation dialog is compiled with native AppleScript; its Download/Cancel buttons and browser behavior are not clicked in this run.",
    "Read-only signature and policy diagnostics are evidence, not a claim that an unsigned launcher passes Gatekeeper. No quarantine attributes or security settings are changed.",
    "Any screenshots are cropped to this test's own synthetic-data windows on the temporary GitHub runner. Screenshot availability is not required for the processing checks.",
]


def command(argv, *, timeout=30, cwd=None, env=None):
    """Keep commands bounded and their logs in the downloadable report."""
    try:
        result = subprocess.run(
            [str(arg) for arg in argv], text=True, encoding="utf-8", errors="replace",
            capture_output=True, timeout=timeout, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        )
        return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"returncode": None, "error": str(error)}


def require_success(result, label):
    if result["returncode"] != 0:
        raise AssertionError(f"{label}: {json.dumps(result, ensure_ascii=False)}")


def import_app(appdir):
    sys.path.insert(0, str(appdir))
    spec = importlib.util.spec_from_file_location("bank_merge", appdir / "bank_merge.py")
    if spec is None or spec.loader is None:
        raise AssertionError("Cannot load the extracted application")
    app = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = app
    spec.loader.exec_module(app)
    return app


def synthetic_inputs(destination):
    samples = destination / "Synthetic 银行样例 with spaces"
    samples.mkdir(exist_ok=False)
    first, second = samples / "Account A.csv", samples / "Account B.csv"
    first.write_text(
        "Date,Description,Amount,Balance,Account Number\n"
        ",Opening balance,,1000,000123\n"
        "01/09/2026,RENT,200,1200,000123\n"
        "02/09/2026,PAYMENT,-50,1150,000123\n"
        ",Closing balance,,1150,000123\n", encoding="utf-8",
    )
    second.write_text(
        "0,4637,BRANCH,0,,BRANCH,0,0\n"
        ",000456,ACC-NO,0,,,0,0\n"
        ",0,OPEN,100,OPEN BALANCE,,0,0\n"
        "HIST,20260930,,10,INTEREST,,30,0\n"
        ",0,CLOSE,110,CLOSE BALANCE,,0,0\n", encoding="utf-8",
    )
    return first, second


def read_import(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    assert len(rows) == 6, "Expected header, two boundary rows and three transactions"
    assert all(len(row) == 7 for row in rows), "Expected seven output columns"
    assert rows[1] == ["01/09/2026"] + [""] * 6, "Opening date row is incorrect"
    assert rows[-1] == ["30/09/2026"] + [""] * 6, "Closing date row is incorrect"
    return rows


def screenshot_window(root, path):
    """Crop the synthetic test window; never capture a user's computer."""
    root.lift()
    root.update_idletasks()
    x, y = max(0, root.winfo_rootx()), max(0, root.winfo_rooty())
    width = min(root.winfo_width(), root.winfo_screenwidth() - x)
    height = min(root.winfo_height(), root.winfo_screenheight() - y)
    if width <= 0 or height <= 0:
        return {"captured": False, "reason": "Test window has no visible capture area"}
    result = command([
        "/usr/sbin/screencapture", "-x", "-R", f"{x},{y},{width},{height}", path,
    ], timeout=8)
    return {"captured": result["returncode"] == 0 and path.is_file(), "command": result}


def gui_child(args):
    """Each GUI probe has its own process so a Tk hang cannot stall the suite."""
    if sys.platform != "darwin":
        raise SystemExit("GUI probe refused: this process is not running on macOS")
    if args.gui_child == "workflow":
        from gui_workflow_checks import workflow_child
        evidence = workflow_child(args, import_app(args.app_dir), screenshot_window)
        print(json.dumps(evidence, ensure_ascii=False))
        return
    import tkinter as tk
    from tkinter import messagebox

    result = {"kind": args.gui_child, "callback_errors": []}
    original_root = tk.Tk

    def fail_dialog(title, message, **unused):
        raise AssertionError(f"Unexpected GUI error dialog: {title}: {message}")

    messagebox.showerror = fail_dialog

    def callback_error(exc_type, value, tb):
        result["callback_errors"].append("".join(traceback.format_exception(exc_type, value, tb)))
        try:
            active_root.destroy()
        except tk.TclError:
            pass

    if args.gui_child == "tk":
        active_root = original_root()
        active_root.withdraw()
        active_root.update()
        result.update(
            tcl_patchlevel=active_root.tk.call("info", "patchlevel"),
            tk_patchlevel=active_root.tk.call("package", "provide", "Tk"),
            window_system=active_root.tk.call("tk", "windowingsystem"),
        )
        active_root.destroy()
    elif args.gui_child == "main":
        app = import_app(args.app_dir)

        def timed_root(*positional, **keywords):
            nonlocal active_root
            active_root = original_root(*positional, **keywords)
            active_root.report_callback_exception = callback_error

            def inspect_and_close():
                try:
                    active_root.geometry("+25+50")
                    active_root.update_idletasks()
                    result["window_title"] = active_root.title()
                    result["top_level_children"] = len(active_root.winfo_children())
                    assert result["top_level_children"] > 0, "Main window is empty"
                    result["screenshot"] = screenshot_window(active_root, args.screenshot)
                finally:
                    active_root.destroy()

            active_root.after(900, inspect_and_close)
            return active_root

        active_root = None
        tk.Tk = timed_root
        try:
            app.gui()
        finally:
            tk.Tk = original_root
        assert result.get("top_level_children", 0) > 0, "Main-window inspection did not finish"
    else:
        import_app(args.app_dir)
        import bank_mapping_ui

        active_root = original_root()
        active_root.withdraw()
        active_root.report_callback_exception = callback_error
        # Keep format-rule writes away from the released application directory.
        bank_mapping_ui.mapping_dialog(active_root, [args.sample_file], args.rules_output, lambda: None)

        def inspect_mapping_and_close():
            try:
                windows = [child for child in active_root.winfo_children() if isinstance(child, tk.Toplevel)]
                assert len(windows) == 1, "Expected the real column-mapping window"
                window = windows[0]
                window.geometry("+25+50")
                window.update_idletasks()
                result["window_title"] = window.title()
                result["mapping_window_count"] = len(windows)
                result["screenshot"] = screenshot_window(window, args.screenshot)
            finally:
                active_root.destroy()

        active_root.after(900, inspect_mapping_and_close)
        active_root.mainloop()
        assert result.get("mapping_window_count") == 1, "Mapping-window inspection did not finish"
    assert not result["callback_errors"], result["callback_errors"]
    print(json.dumps(result, ensure_ascii=False))


def write_summary(destination, report):
    state = report.get("result", "failed")
    lines = [
        "## Bank-tool macOS release checks", "",
        f"**Result: {state}**", "",
        f"- Real macOS: `{report['actual_macos']}`",
        f"- Architecture: `{report['architecture']}`",
        f"- Python executable: `{report['python_executable']}`",
        f"- Release SHA256: `{report.get('package_sha256', 'not available')}`", "",
        "| Check | Result |", "| --- | --- |",
    ]
    for check in report["checks"]:
        lines.append(f"| {check['name']} | Passed |")
    if report.get("error"):
        lines += ["", "The run failed. See report.json and the failed step logs for details."]
    lines += ["", "### Limits of this result", ""]
    lines.extend(f"- {item}" for item in report["limitations"])
    lines += ["", "Download this job's artifact for report.json, synthetic outputs, and any available window screenshots."]
    (destination / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path)
    parser.add_argument("--artifacts", type=Path, default=Path("mac-test-artifacts"))
    parser.add_argument("--require-macos", action="store_true")
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--gui-child", choices=("tk", "main", "mapping", "workflow"), help=argparse.SUPPRESS)
    parser.add_argument("--app-dir", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--sample-file", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--rules-output", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--screenshot", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.gui_child:
        gui_child(args)
        return 0
    if args.require_macos and sys.platform != "darwin":
        parser.error("NOT RUN: this is not macOS. No Mac validation result can be claimed.")
    if args.gui and sys.platform != "darwin":
        parser.error("Mac GUI checks require a real macOS process")
    if args.package is None:
        parser.error("--package is required")

    destination = args.artifacts.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    report = {
        "platform": platform.platform(), "architecture": platform.machine(),
        "python": sys.version, "python_executable": sys.executable,
        "actual_macos": sys.platform == "darwin", "checks": [],
        "limitations": list(LIMITATIONS),
    }

    def passed(name, **evidence):
        report["checks"].append({"name": name, "passed": True, **evidence})

    try:
        package = args.package.resolve(strict=True)
        report["package_sha256"] = hashlib.sha256(package.read_bytes()).hexdigest()
        unpacked = destination / "extracted release"
        unpacked.mkdir(exist_ok=False)
        with ZipFile(package) as archive:
            assert archive.testzip() is None, "ZIP CRC failure"
            for item in archive.infolist():
                parts = PurePosixPath(item.filename)
                assert not parts.is_absolute() and ".." not in parts.parts and "\\" not in item.filename, "Unsafe archive path"
                assert parts.parts and parts.parts[0] == "bank_converter", "Unexpected release root"
                assert not stat.S_ISLNK(item.external_attr >> 16), "Release must not contain symlinks"
            script = archive.read("bank_converter/start_mac.command")
            script.decode("utf-8")
            assert not script.startswith(b"\xef\xbb\xbf") and b"\r" not in script, "Launcher must be UTF-8, no BOM, LF only"
            mode = archive.getinfo("bank_converter/start_mac.command").external_attr >> 16
            assert mode & 0o111, "Mac executable bits are missing from the archive"
            if sys.platform != "darwin":
                archive.extractall(unpacked)
        passed("ZIP integrity and Mac launcher encoding/metadata", launcher_sha256=hashlib.sha256(script).hexdigest())

        if sys.platform == "darwin":
            extraction = command(["/usr/bin/ditto", "-x", "-k", package, unpacked])
            require_success(extraction, "Native macOS release extraction")
            extracted_launcher = unpacked / "bank_converter" / "start_mac.command"
            actual_mode = stat.S_IMODE(extracted_launcher.stat().st_mode)
            assert actual_mode & 0o111 and os.access(extracted_launcher, os.X_OK), "Extracted launcher is not executable"
            passed("Native ditto extraction preserves launch permissions", extracted_mode=oct(actual_mode))
        else:
            report["limitations"].append("This portable run skipped ditto, shell launch, native AppleScript, security diagnostics and Mac GUI checks; it is not a Mac test.")

        appdir = destination / "搬家 测试 workspace" / "银行工具 bank converter"
        appdir.parent.mkdir(exist_ok=False)
        (unpacked / "bank_converter").rename(appdir)
        passed("Released app relocates into a Chinese and spaced directory")
        regression_script = Path(__file__).resolve().with_name("test_formats.py")
        assert regression_script.is_file(), "Release parser regression script is missing"
        regression_env = dict(os.environ, BANK_TOOL_ROOT=str(appdir))
        regressions = command([sys.executable, "-B", regression_script], env=regression_env, timeout=60)
        report["parser_regressions"] = regressions
        require_success(regressions, "Synthetic parser regressions against the extracted release")
        regression_count = re.search(r"Ran (\d+) tests? in ([\d.]+)s", regressions["stdout"] + regressions["stderr"])
        assert regression_count and int(regression_count.group(1)) > 0, "Parser regression result did not include a test count"
        passed("Synthetic format and validation regressions run against the extracted release",
               test_count=int(regression_count.group(1)), seconds=float(regression_count.group(2)))
        app = import_app(appdir)
        first, second = synthetic_inputs(destination)
        before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (first, second)}
        transactions, summary = app.prepare(files=[first, second], profiles=[])
        assert len(transactions) == 3 and summary["net"] == "160.00", "Unexpected synthetic transaction totals"
        assert all(source["balance_difference"] == "0.00" for source in summary["sources"]), "Synthetic accounts do not reconcile"
        output = app.write_result(transactions, summary, destination / "generated core output")
        expected_rows = read_import(output / "ERP_import_2026-09.csv")
        passed("Two synthetic bank formats produce three transactions and balanced accounts")
        passed("IQ output order is month start, all transactions, month end; other boundary fields are blank")

        if sys.platform == "darwin":
            report["macos_version"] = command(["/usr/bin/sw_vers"])
            launcher = appdir / "start_mac.command"
            syntax = command(["/bin/sh", "-n", launcher])
            require_success(syntax, "Native /bin/sh syntax")
            passed("Native macOS launcher shell syntax")

            launch = command([
                launcher, "--files", first, second, "--output", destination / "generated launcher output",
            ], cwd=destination, timeout=40)
            report["launcher_cli"] = launch
            require_success(launch, "Real extracted launcher CLI")
            launch_result = json.loads(launch["stdout"])
            assert launch_result["count"] == 3 and launch_result["net"] == "160.00", "Launcher did not run the requested conversion"
            launcher_rows = read_import(Path(launch_result["output"]) / "ERP_import_2026-09.csv")
            assert launcher_rows == expected_rows, "Launcher output differs from the checked core output"
            passed("Real executable launcher converts synthetic inputs after relocation")

            apple_lines = re.findall(r"-e '([^']*)'", script.decode("utf-8"))
            assert apple_lines, "The native installation-dialog AppleScript was not found"
            applefile = destination / "download_dialog.applescript"
            applefile.write_text("\n".join(apple_lines) + "\n", encoding="utf-8")
            compiled = command(["/usr/bin/osacompile", "-o", destination / "download_dialog.scpt", applefile])
            require_success(compiled, "Native installation-dialog compilation")
            passed("Original download-dialog AppleScript compiles on macOS")

            report["security_diagnostics"] = {
                "attributes": command(["/usr/bin/xattr", "-l", launcher]),
                "assessment": command(["/usr/sbin/spctl", "--assess", "--type", "execute", "-vv", launcher]),
                "signature": command(["/usr/bin/codesign", "-dv", "--verbose=4", launcher]),
            }

        if args.gui:
            for kind, label in (
                ("tk", "Actual macOS Tk root creates, updates and closes"),
                ("main", "Real application main window initializes and closes"),
                ("mapping", "Real column-mapping window initializes and closes"),
                ("workflow", "GUI Generate and mapping-save workflow works with synthetic inputs"),
            ):
                probe = command([
                    sys.executable, "-B", Path(__file__).resolve(), "--gui-child", kind,
                    "--app-dir", appdir, "--sample-file", first,
                    "--rules-output", destination / f"synthetic-{kind}-format-rules.json",
                    "--screenshot", destination / f"synthetic-{kind}-window.png",
                ], timeout=25)
                report[f"gui_{kind}"] = probe
                require_success(probe, label)
                details = json.loads(probe["stdout"])
                passed(label, evidence=details)
        else:
            report["limitations"].append("GUI checks were not requested in this run.")

        assert all(hashlib.sha256(path.read_bytes()).hexdigest() == before[path.name] for path in (first, second)), "Input files were modified"
        passed("Original synthetic input files remain byte-for-byte unchanged")
        report["result"] = "passed_with_listed_limits" if report["actual_macos"] else "portable_checks_passed_not_macos"
        returncode = 0
    except Exception as error:
        report["result"] = "failed"
        report["error"] = f"{type(error).__name__}: {error}"
        report["traceback"] = traceback.format_exc()
        returncode = 1
    finally:
        (destination / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        write_summary(destination, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
