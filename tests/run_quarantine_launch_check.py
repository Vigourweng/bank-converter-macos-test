"""Observe LaunchServices on verified disposable copies on GitHub-hosted Macs.

This is an evidence-collection experiment, not a security override or a claim
that the user-visible Lark download problem has been reproduced or repaired.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import stat
import subprocess
import sys
import time
import uuid
from zipfile import ZipFile


AGENT = 'BankToolCIDiagnostic'
LIMITS = [
    'Raw quarantine flag strings 0083/0087 are synthetic experimental inputs. They do not reproduce Lark metadata or an actual browser download.',
    'The clean and quarantined cases run on separate fresh runners to avoid dismissing dialogs or reusing a previous approval.',
    'Opening a document with Terminal through LaunchServices approximates Finder handing a .command file to Terminal; it is not a literal Finder double-click.',
    'A zero return code from open only means LaunchServices accepted the request, not that the program ran or Gatekeeper accepted it.',
    'Screenshots must be visually reviewed before claiming that the same damaged-file dialog appeared. No automatic popup matching is claimed.',
    'Only the temporary test launcher receives a quarantine attribute. No security settings, trust exceptions, quarantine removals or dialog clicks are performed.',
    'Whole-screen captures are restricted to the empty temporary GitHub runner. They must never be run on a user desktop using this harness.',
]


def command(argv, *, timeout=20, limit=65536):
    try:
        p = subprocess.run([str(x) for x in argv], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=timeout)
        return {'returncode': p.returncode, 'stdout': p.stdout[:limit],
                'stderr': p.stderr[:limit],
                'stdout_truncated': len(p.stdout) > limit,
                'stderr_truncated': len(p.stderr) > limit}
    except (OSError, subprocess.TimeoutExpired) as e:
        return {'returncode': None, 'error': str(e)}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def required(result, label):
    if result['returncode'] != 0:
        raise RuntimeError(label + ': ' + json.dumps(result, ensure_ascii=False))


def manifest(root):
    return {p.relative_to(root).as_posix(): sha(p)
            for p in root.rglob('*') if p.is_file() and not p.is_symlink()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--expected-package-sha256', required=True)
    parser.add_argument('--expected-launcher-sha256', required=True)
    parser.add_argument('--case', choices=('clean', 'quarantine0083', 'quarantine0087'), required=True)
    parser.add_argument('--artifacts', type=Path, required=True)
    args = parser.parse_args()
    # This harness may open windows and capture the full display. It therefore
    # refuses user computers and self-hosted runners; the workflow passes the
    # trusted runner.environment context into this dedicated variable.
    if (sys.platform != 'darwin' or os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('BANK_DIAGNOSTIC_RUNNER_KIND') != 'github-hosted'
            or os.environ.get('RUNNER_OS') != 'macOS'):
        parser.error('Refusing: this observation is only for a disposable GitHub-hosted Mac.')
    destination = args.artifacts.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    report = {'experiment': 'LaunchServices quarantine observation',
              'case': args.case, 'actual_macos': True,
              'architecture': platform.machine(), 'platform': platform.platform(),
              'limitations': LIMITS, 'screenshots': [], 'result': 'incomplete'}
    code = 1
    try:
        package = args.package.resolve(strict=True)
        report['package_sha256'] = sha(package)
        assert report['package_sha256'] == args.expected_package_sha256.lower(), 'Package differs from reviewed release'
        with ZipFile(package) as archive:
            assert archive.testzip() is None, 'ZIP CRC failure'
            for item in archive.infolist():
                parts = PurePosixPath(item.filename)
                assert parts.parts and parts.parts[0] == 'bank_converter'
                assert not parts.is_absolute() and '..' not in parts.parts and '\\' not in item.filename
                assert not stat.S_ISLNK(item.external_attr >> 16), 'Symlinks not accepted in diagnostic package'
        extraction = destination / ('verified-copy-' + uuid.uuid4().hex)
        extraction.mkdir()
        extracted = command(['/usr/bin/ditto', '-x', '-k', package, extraction])
        required(extracted, 'Native extraction')
        app = (extraction / 'bank_converter').resolve(strict=True)
        assert destination in app.parents and not app.is_symlink()
        launcher = (app / 'start_mac.command').resolve(strict=True)
        assert launcher.parent == app and not launcher.is_symlink()
        report['launcher_sha256'] = sha(launcher)
        assert report['launcher_sha256'] == args.expected_launcher_sha256.lower(), 'Launcher differs from reviewed release'
        assert os.access(launcher, os.X_OK), 'Extracted launcher is not executable'
        original = manifest(app)
        report['file_manifest_before'] = original
        report['gatekeeper_status_before'] = command(['/usr/sbin/spctl', '--status'])
        report['xattrs_before'] = command(['/usr/bin/xattr', '-l', launcher])
        attribute_names = command(['/usr/bin/xattr', launcher])
        required(attribute_names, 'Read original extended attribute names')
        assert 'com.apple.quarantine' not in attribute_names['stdout'].splitlines(), 'Fresh copy unexpectedly has quarantine; refusing to clear it'
        if args.case != 'clean':
            flags = args.case.removeprefix('quarantine')
            value = f'{flags};{int(time.time()):x};{AGENT};{str(uuid.uuid4()).upper()}'
            # This only ADDS quarantine to an otherwise identical temporary
            # copy. It never clears an attribute or pretends to be Lark.
            addition = command(['/usr/bin/xattr', '-w', 'com.apple.quarantine', value, launcher])
            required(addition, 'Adding synthetic quarantine to disposable launcher')
            readback = command(['/usr/bin/xattr', '-p', 'com.apple.quarantine', launcher])
            required(readback, 'Read back synthetic quarantine')
            actual = readback['stdout'].rstrip('\n')
            assert actual == value, 'Synthetic quarantine was not preserved'
            report['quarantine_written'] = actual
        else:
            report['quarantine_written'] = None
        assert manifest(app) == original, 'Adding metadata changed file contents'
        report['assessment_before_launch'] = command(['/usr/sbin/spctl', '--assess', '--type', 'execute', '-vv', launcher])
        report['signature'] = command(['/usr/bin/codesign', '-dv', '--verbose=4', launcher])
        report['macos_version'] = command(['/usr/bin/sw_vers'])
        # Do not execute the shell script ourselves: send it as a document to
        # the normal signed Terminal application through LaunchServices.
        started = time.monotonic()
        report['launch_request'] = command(['/usr/bin/open', '-a', 'Terminal.app', launcher], timeout=12)
        for target_second in (1, 3, 6):
            remaining = started + target_second - time.monotonic()
            if remaining > 0:
                time.sleep(min(remaining, 3))
            capture = destination / f'launch-{target_second:02d}s.png'
            result = command(['/usr/sbin/screencapture', '-x', '-t', 'png', capture], timeout=8)
            report['screenshots'].append({'at_second': target_second, 'file': capture.name,
                                          'captured': result['returncode'] == 0 and capture.is_file(),
                                          'command': result})
        processes = command(['/bin/ps', '-axo', 'pid,ppid,comm,args'])
        if processes['returncode'] == 0:
            processes['stdout'] = '\n'.join(line for line in processes['stdout'].splitlines()
                if str(app) in line or 'CoreServicesUIAgent' in line or '/Terminal.app/' in line)
        report['relevant_processes'] = processes
        report['security_log'] = command(['/usr/bin/log', 'show', '--last', '2m', '--style', 'compact',
            '--predicate', 'process == "syspolicyd" OR process == "CoreServicesUIAgent" OR subsystem == "com.apple.security.assessment"'], timeout=20)
        report['xattrs_after'] = command(['/usr/bin/xattr', '-l', launcher])
        report['gatekeeper_status_after'] = command(['/usr/sbin/spctl', '--status'])
        report['file_manifest_after'] = manifest(app)
        assert report['file_manifest_after'] == original, 'Released source contents changed during launch observation'
        assert sha(package) == report['package_sha256'], 'Original ZIP changed'
        report['popup_match'] = 'not_classified_requires_visual_review'
        report['result'] = ('observation_collected_requires_visual_review'
                            if any(s['captured'] for s in report['screenshots']) else
                            'inconclusive_screenshot_capture_unavailable')
        code = 0 if any(s['captured'] for s in report['screenshots']) else 1
    except Exception as e:
        report['result'] = 'diagnostic_failed'
        report['error'] = f'{type(e).__name__}: {e}'
    finally:
        (destination / 'quarantine-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        summary = ['## LaunchServices quarantine observation', '',
                   '**This job collects evidence; success does not mean the app passed Gatekeeper.**', '',
                   f"- Case: `{args.case}`", f"- Result: `{report['result']}`",
                   f"- Actual macOS architecture: `{report['architecture']}`",
                   f"- Release SHA256: `{report.get('package_sha256', 'unavailable')}`",
                   f"- Launcher SHA256: `{report.get('launcher_sha256', 'unavailable')}`",
                   f"- Agent: `{AGENT}` (synthetic, never Lark)", '',
                   'Download the artifact and visually inspect launch-01s.png, launch-03s.png and launch-06s.png.', '',
                   '### Scope and limits', ''] + ['- ' + s for s in LIMITS]
        if 'error' in report:
            summary += ['', 'Diagnostic error: `' + report['error'].replace('`', "'") + '`']
        (destination / 'summary.md').write_text('\n'.join(summary) + '\n', encoding='utf-8')
        print(json.dumps({'case': args.case, 'result': report['result'], 'error': report.get('error')}, ensure_ascii=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
