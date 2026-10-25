"""Collect Python Launcher observations on disposable GitHub-hosted Macs only.

No security exception is installed, no extended attribute is removed, no alert
is clicked, and the bank application is never invoked through an interpreter.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import plistlib
import signal
import stat
import subprocess
import sys
import time
import uuid
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

LAUNCHER_APP = Path('/Applications/Python 3.13/Python Launcher.app')
LAUNCHER_DOMAIN = 'org.python.PythonLauncher'
SCRIPT_FACTORY_KEY = 'Python Script'
LIMITS = [
    'These are synthetic quarantine inputs, not an actual Lark or browser download.',
    'Each case uses a fresh GitHub-hosted macOS runner. No personal account or data is used.',
    'The candidate preserves the reviewed release bytes and uses the exact reviewed start_mac.pyw, either already included or added once by this experiment.',
    'LaunchServices opens the document explicitly with official Python Launcher.app, equivalent to Open With, not a literal Finder double-click.',
    'A normal user may need to choose Open With > Python Launcher once, or set that file association. This experiment does not set associations.',
    'Python Launcher may request ordinary permission to control Terminal. No permission is pregranted or accepted in this experiment.',
    'With --without-terminal, only the normal Python Launcher Python Script/with_terminal presentation preference is set to false before first launch. The default experiment remains available without that flag.',
    'With --skip-file-binding-reminder, the normal Python Launcher SkipFileBindingTest reminder preference is set to true. No actual file association is created or changed.',
    'A zero exit status for open means a request was accepted, not that Gatekeeper approved it or the bank window opened.',
    'Screenshots require visual review. A completed CI job is evidence collection, not a compatibility or security pass.',
    'No quarantine attribute is removed, no Gatekeeper setting changes, and no warning or permission dialog is clicked.',
    'No bank workflow interactions or numerical correctness checks are performed by this launch observation.',
]


def command(argv, timeout=20, limit=60000):
    began = time.monotonic()
    try:
        p = subprocess.run([str(x) for x in argv], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=timeout)
        return dict(returncode=p.returncode, stdout=p.stdout[:limit], stderr=p.stderr[:limit],
                    duration_seconds=round(time.monotonic()-began, 3),
                    stdout_truncated=len(p.stdout)>limit, stderr_truncated=len(p.stderr)>limit)
    except (OSError, subprocess.TimeoutExpired) as e:
        return dict(returncode=None, error=str(e), duration_seconds=round(time.monotonic()-began, 3))


def required(result, label):
    if result.get('returncode') != 0:
        raise RuntimeError(label + ': ' + json.dumps(result, ensure_ascii=False))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest(root):
    return {p.relative_to(root).as_posix(): sha(p) for p in root.rglob('*')
            if p.is_file() and not p.is_symlink()}


def xattrs(path):
    return command(['/usr/bin/xattr', '-lr', path])


def quarantine(path):
    names = command(['/usr/bin/xattr', path])
    required(names, 'Read extended attribute names')
    if 'com.apple.quarantine' not in names['stdout'].splitlines():
        return None
    value = command(['/usr/bin/xattr', '-p', 'com.apple.quarantine', path])
    required(value, 'Read quarantine value')
    return value['stdout'].rstrip('\n')


def processes(app_dir):
    observed = command(['/bin/ps', '-axo', 'pid,ppid,comm,args'], limit=250000)
    if observed.get('returncode') == 0:
        observed['stdout'] = '\n'.join(s for s in observed['stdout'].splitlines()
            if any(t in s for t in (str(app_dir), 'Python Launcher.app', 'CoreServicesUIAgent',
                                    '/Terminal.app/', 'tccd', 'syspolicyd')))
    return observed


def configure_launcher_presentation(out, report, *, without_terminal=True, skip_file_binding_reminder=False):
    """Set an ordinary app preference, never a privacy or security permission."""
    bundle = plistlib.loads((LAUNCHER_APP / 'Contents/Info.plist').read_bytes())
    assert bundle.get('CFBundleIdentifier') == LAUNCHER_DOMAIN, 'Unexpected preference domain'
    running = command(['/bin/ps', '-axo', 'comm,args'])
    required(running, 'Verify Python Launcher has not already started')
    assert str(LAUNCHER_APP) not in running['stdout'], 'Python Launcher already running; refusing preference change'
    before = command(['/usr/bin/defaults', 'export', LAUNCHER_DOMAIN, '-'])
    report['launcher_presentation_preference'] = dict(
        changed=False, domain=LAUNCHER_DOMAIN, factoryKey=SCRIPT_FACTORY_KEY,
        preference='with_terminal', requested_value=False if without_terminal else None,
        file_binding_reminder_preference='SkipFileBindingTest',
        file_binding_reminder_requested_value=True if skip_file_binding_reminder else None,
        actual_file_association_changed=False,
        scope='Normal application presentation preference only; no TCC or security settings changed.',
        export_before=before)
    settings = {}
    if without_terminal:
        settings[SCRIPT_FACTORY_KEY] = {'with_terminal': False}
    if skip_file_binding_reminder:
        settings['SkipFileBindingTest'] = True
    assert settings, 'No normal app preference was requested'
    report['launcher_presentation_preference']['settings_requested'] = settings
    preference_file = out / 'python-launcher-presentation.plist'
    preference_file.write_bytes(plistlib.dumps(settings, fmt=plistlib.FMT_XML, sort_keys=True))
    imported = command(['/usr/bin/defaults', 'import', LAUNCHER_DOMAIN, preference_file])
    report['launcher_presentation_preference']['import_result'] = imported
    required(imported, 'Import ordinary Python Launcher presentation preference')
    report['launcher_presentation_preference']['changed'] = True
    exported = command(['/usr/bin/defaults', 'export', LAUNCHER_DOMAIN, '-'])
    report['launcher_presentation_preference']['export_after'] = exported
    required(exported, 'Read back ordinary Python Launcher preference')
    readback = plistlib.loads(exported['stdout'].encode('utf-8'))
    if without_terminal:
        selected = readback.get(SCRIPT_FACTORY_KEY, {}).get('with_terminal')
        assert selected is False, 'Python Script/with_terminal did not read back as boolean false'
        report['launcher_presentation_preference']['verified_value'] = selected
    if skip_file_binding_reminder:
        selected = readback.get('SkipFileBindingTest')
        assert selected is True, 'SkipFileBindingTest did not read back as boolean true'
        report['launcher_presentation_preference']['file_binding_reminder_verified_value'] = selected


def capture(path, started, target):
    # Popen prevents screencapture from blocking the scheduled observation loop.
    requested = time.monotonic()
    result = dict(file=path.name, scheduled_seconds=target,
                  requested_elapsed_seconds=round(requested-started, 3))
    try:
        p = subprocess.Popen(['/usr/sbin/screencapture', '-x', '-t', 'png', str(path)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.PIPE)
        result['_process'] = p
        result['_requested_monotonic'] = requested
    except OSError as e:
        result['error'] = str(e)
    return result


def finish_capture(record, started):
    p = record.pop('_process', None)
    requested = record.pop('_requested_monotonic', None)
    if p is None:
        return
    try:
        _, err = p.communicate(timeout=0.1)
        record.update(returncode=p.returncode, stderr=err.decode('utf-8', 'replace')[:4000],
                      completion_observed_elapsed_seconds=round(time.monotonic()-started, 3))
    except subprocess.TimeoutExpired:
        if time.monotonic()-requested > 8:
            p.kill()
            _, err = p.communicate(timeout=2)
            record.update(returncode=p.returncode, error='Capture exceeded 8 seconds',
                          stderr=err.decode('utf-8', 'replace')[:4000])
        else:
            record['_process'] = p
            record['_requested_monotonic'] = requested


def capture_preferences_ui(out, report):
    """Observe normal Preferences, then stop only that verified app process."""
    record = dict(pre_document_app_launch_performed=True,
                  limitation='This optional preflight opens the app once before the document experiment.')
    report['preferences_ui_preflight'] = record
    record['open_request'] = command(['/usr/bin/open', '-a', LAUNCHER_APP], timeout=10)
    required(record['open_request'], 'Open ordinary Python Launcher Preferences')
    started = time.monotonic()
    time.sleep(2)
    screen = capture(out / 'launcher-preferences.png', started, 2)
    record['screenshot'] = screen
    try:
        while '_process' in screen:
            finish_capture(screen, started)
            if '_process' in screen:
                time.sleep(0.1)
    finally:
        pending = screen.pop('_process', None)
        screen.pop('_requested_monotonic', None)
        if pending is not None:
            pending.kill()
            pending.communicate(timeout=3)
    screen['file_created'] = (out / screen['file']).is_file()
    observed = command(['/bin/ps', '-ww', '-axo', 'pid=,comm='])
    required(observed, 'Identify exact Python Launcher process')
    executable = str(LAUNCHER_APP / 'Contents/MacOS/Python Launcher')
    matches = []
    for line in observed['stdout'].splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[1] == executable:
            matches.append(int(parts[0]))
    record['verified_launcher_pids'] = matches
    assert len(matches) == 1, 'Cannot uniquely verify Python Launcher process; refusing to terminate anything'
    pid = matches[0]
    confirmed = command(['/bin/ps', '-ww', '-p', pid, '-o', 'comm='])
    record['process_identity_before_termination'] = confirmed
    required(confirmed, 'Reverify exact app identity before closing Preferences')
    assert confirmed['stdout'].strip() == executable, 'Process identity changed; refusing termination'
    os.kill(pid, signal.SIGTERM)
    record['termination_signal'] = 'SIGTERM sent only to the verified Python Launcher process'
    deadline = time.monotonic()+5
    while time.monotonic() < deadline:
        remaining = command(['/bin/ps', '-ww', '-p', pid, '-o', 'comm='])
        if remaining.get('returncode') == 1:
            record['launcher_stopped_before_document_open'] = True
            return
        time.sleep(0.2)
    raise RuntimeError('Python Launcher did not stop after Preferences capture; document launch not attempted')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--expected-package-sha256', required=True)
    parser.add_argument('--wrapper', type=Path, required=True)
    parser.add_argument('--expected-wrapper-sha256', required=True)
    parser.add_argument('--case', choices=['clean', 'quarantine0083', 'quarantine0087'], required=True)
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--without-terminal', action='store_true',
                        help='Set only Python Launcher Python Script/with_terminal=false before first open')
    parser.add_argument('--skip-file-binding-reminder', action='store_true',
                        help='Set only the normal SkipFileBindingTest reminder preference; does not change associations')
    parser.add_argument('--capture-preferences', action='store_true',
                        help='Capture the normal app Preferences before document open; requires --skip-file-binding-reminder')
    args = parser.parse_args()
    if args.capture_preferences and not args.skip_file_binding_reminder:
        parser.error('--capture-preferences requires --skip-file-binding-reminder to avoid an ordinary reminder')
    if (sys.platform != 'darwin' or os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('BANK_PY_LAUNCHER_RUNNER_KIND') != 'github-hosted'
            or os.environ.get('RUNNER_OS') != 'macOS'):
        parser.error('This experiment refuses user computers and self-hosted runners.')
    out = args.artifacts.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = dict(experiment='Official Python Launcher document opening', case=args.case,
                  platform=platform.platform(), architecture=platform.machine(),
                  limitations=LIMITS, screenshots=[], result='incomplete',
                  presentation_mode='without_terminal' if args.without_terminal else 'factory_defaults',
                  file_binding_reminder_mode='skip_normal_reminder' if args.skip_file_binding_reminder else 'factory_defaults',
                  launcher_presentation_preference=dict(changed=False, factoryKey=SCRIPT_FACTORY_KEY,
                                                        preference='with_terminal'))
    exit_code = 1
    try:
        package = args.package.resolve(strict=True)
        wrapper = args.wrapper.resolve(strict=True)
        report['release_sha256'] = sha(package)
        report['wrapper_sha256'] = sha(wrapper)
        assert report['release_sha256'] == args.expected_package_sha256.lower(), 'Unreviewed release hash'
        assert report['wrapper_sha256'] == args.expected_wrapper_sha256.lower(), 'Unreviewed wrapper hash'
        expected = {}
        wrapper_in_release = False
        candidate_zip = out / '银行工具 下载候选.zip'
        with ZipFile(package) as source, ZipFile(candidate_zip, 'w', compression=ZIP_DEFLATED) as candidate:
            assert source.testzip() is None, 'Invalid release CRC'
            assert len(source.namelist()) == len(set(source.namelist())), 'Duplicate ZIP entry'
            for item in source.infolist():
                name = PurePosixPath(item.filename)
                assert name.parts and name.parts[0] == 'bank_converter'
                assert not name.is_absolute() and '..' not in name.parts and '\\' not in item.filename
                assert not item.is_dir() and stat.S_ISREG(item.external_attr >> 16), 'Only regular files allowed'
                data = source.read(item)
                if item.filename == 'bank_converter/start_mac.pyw':
                    assert hashlib.sha256(data).hexdigest() == report['wrapper_sha256'], 'Released wrapper differs from reviewed wrapper'
                    wrapper_in_release = True
                expected[str(PurePosixPath(*name.parts[1:]))] = hashlib.sha256(data).hexdigest()
                candidate.writestr(item, data)
            if not wrapper_in_release:
                entry = ZipInfo('bank_converter/start_mac.pyw', date_time=(2026, 1, 1, 0, 0, 0))
                entry.create_system = 3
                entry.external_attr = (stat.S_IFREG | 0o644) << 16
                entry.compress_type = ZIP_DEFLATED
                candidate.writestr(entry, wrapper.read_bytes())
                expected['start_mac.pyw'] = sha(wrapper)
        report['release_contains_wrapper'] = wrapper_in_release
        report['candidate_wrapper_origin'] = 'reviewed_release' if wrapper_in_release else 'added_once_by_experiment'
        report['candidate_archive_sha256'] = sha(candidate_zip)
        report['archive_xattrs_initial'] = xattrs(candidate_zip)
        assert quarantine(candidate_zip) is None, 'Unexpected initial quarantine; refusing to remove it'
        value = None
        if args.case != 'clean':
            value = '{};{:x};BankPythonLauncherCI;{}'.format(args.case[-4:], int(time.time()), str(uuid.uuid4()).upper())
            required(command(['/usr/bin/xattr', '-w', 'com.apple.quarantine', value, candidate_zip]),
                     'Assign synthetic quarantine to test archive')
        report['synthetic_archive_quarantine'] = value
        report['archive_xattrs_before_extract'] = xattrs(candidate_zip)
        extraction = out / '中文 路径' / '银行工具 实验'
        extraction.mkdir(parents=True)
        required(command(['/usr/bin/ditto', '-x', '-k', candidate_zip, extraction]), 'Native ZIP extraction')
        app_dir = (extraction / 'bank_converter').resolve(strict=True)
        document = app_dir / 'start_mac.pyw'
        assert manifest(app_dir) == expected, 'Extracted release/wrapper bytes differ'
        report['extracted_manifest_before'] = expected
        report['extracted_xattrs_initial'] = xattrs(app_dir)
        inherited = quarantine(document)
        report['wrapper_quarantine_after_extract'] = inherited
        # Some native command-line extraction paths do not propagate quarantine.
        # If absent, add the controlled case to the fresh test document only.
        # Never replace an inherited value or remove attributes.
        if value is not None and inherited is None:
            required(command(['/usr/bin/xattr', '-w', 'com.apple.quarantine', value, document]),
                     'Assign controlled synthetic quarantine to fresh test document')
            report['wrapper_quarantine_added_because_not_inherited'] = True
        else:
            report['wrapper_quarantine_added_because_not_inherited'] = False
        if value is None:
            assert inherited is None, 'Clean case has unexpected quarantine; refusing to remove it'
        report['wrapper_quarantine_before_open'] = quarantine(document)
        report['extracted_xattrs_before_open'] = xattrs(app_dir)
        report['macos_version'] = command(['/usr/bin/sw_vers'])
        report['gatekeeper_status_before'] = command(['/usr/sbin/spctl', '--status'])
        report['launcher_signature_details'] = command(['/usr/bin/codesign', '-dv', '--verbose=4', LAUNCHER_APP])
        factory_file = LAUNCHER_APP / 'Contents/Resources/factorySettings.plist'
        report['installed_launcher_factory_settings_sha256'] = sha(factory_file)
        report['installed_launcher_factory_settings'] = plistlib.loads(factory_file.read_bytes())
        report['installed_launcher_document_types'] = plistlib.loads(
            (LAUNCHER_APP / 'Contents/Info.plist').read_bytes()).get('CFBundleDocumentTypes', [])
        report['launcher_signature_verify'] = command(['/usr/bin/codesign', '--verify', '--deep', '--strict', '--verbose=2', LAUNCHER_APP])
        required(report['launcher_signature_verify'], 'Official Python Launcher signature verification')
        details = report['launcher_signature_details'].get('stderr', '')
        assert 'Python Software Foundation' in details, 'Python Launcher signer is not the expected publisher'
        report['launcher_gatekeeper_assessment'] = command(['/usr/sbin/spctl', '--assess', '--type', 'execute', '--verbose=4', LAUNCHER_APP])
        required(report['launcher_gatekeeper_assessment'], 'Official Python Launcher Gatekeeper assessment')
        report['launcher_xattrs_before'] = xattrs(LAUNCHER_APP)
        report['processes_before'] = processes(app_dir)
        if args.without_terminal or args.skip_file_binding_reminder:
            configure_launcher_presentation(out, report, without_terminal=args.without_terminal,
                                             skip_file_binding_reminder=args.skip_file_binding_reminder)
        if args.capture_preferences:
            capture_preferences_ui(out, report)
        report['launch_argv'] = ['/usr/bin/open', '-a', str(LAUNCHER_APP), str(document)]
        opened_stdout = out / 'open-stdout.txt'
        opened_stderr = out / 'open-stderr.txt'
        started = time.monotonic()
        report['launch_wall_time_utc'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        with opened_stdout.open('wb') as stdout, opened_stderr.open('wb') as stderr:
            request = subprocess.Popen(report['launch_argv'], stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
            pending = [0, 2, 5, 10, 20, 30]
            while pending or any('_process' in r for r in report['screenshots']):
                elapsed = time.monotonic()-started
                while pending and elapsed >= pending[0]:
                    target = pending.pop(0)
                    record = capture(out / ('launch-{:02d}s.png'.format(target)), started, target)
                    report['screenshots'].append(record)
                for record in report['screenshots']:
                    if '_process' in record:
                        finish_capture(record, started)
                if elapsed > 42:
                    raise RuntimeError('Screenshot observation loop exceeded bound')
                time.sleep(0.1)
            report['launch_request_returncode_at_end'] = request.poll()
            if request.poll() is None:
                request.terminate()
                request.wait(timeout=3)
                report['open_request_was_terminated_after_observation'] = True
        for record in report['screenshots']:
            record['file_created'] = (out / record['file']).is_file()
        report['processes_after'] = processes(app_dir)
        # Avoid AppleEvents that could themselves introduce a permission dialog.
        report['window_observation'] = 'Screenshots only; no AppleEvents sent to apps or System Events.'
        report['security_log'] = command(['/usr/bin/log', 'show', '--last', '2m', '--style', 'compact',
            '--predicate', 'process == "syspolicyd" OR process == "CoreServicesUIAgent" OR process == "tccd"'], timeout=20)
        report['extracted_xattrs_after_open'] = xattrs(app_dir)
        report['launcher_xattrs_after'] = xattrs(LAUNCHER_APP)
        report['archive_xattrs_after'] = xattrs(candidate_zip)
        report['gatekeeper_status_after'] = command(['/usr/sbin/spctl', '--status'])
        report['extracted_manifest_after'] = manifest(app_dir)
        assert report['extracted_manifest_after'] == expected, 'Released content changed during observation'
        assert sha(candidate_zip) == report['candidate_archive_sha256'], 'Candidate archive changed'
        assert sha(package) == report['release_sha256'], 'Original release changed'
        assert sha(wrapper) == report['wrapper_sha256'], 'Original wrapper changed'
        report['visual_classification'] = 'pending_human_review_of_all_screenshots'
        report['result'] = ('observation_collected_requires_visual_review' if any(
            r.get('file_created') and r.get('returncode') == 0 for r in report['screenshots'])
            else 'inconclusive_screenshots_unavailable')
        exit_code = 0 if report['result'] == 'observation_collected_requires_visual_review' else 1
    except Exception as exc:
        report['error'] = '{}: {}'.format(type(exc).__name__, exc)
        report['result'] = 'experiment_failed_or_blocked'
    finally:
        # Ensure incomplete screenshot subprocesses cannot leak unserializable
        # objects into the report if the observation failed mid-loop.
        for record in report['screenshots']:
            child = record.pop('_process', None)
            record.pop('_requested_monotonic', None)
            if child is not None:
                child.kill()
                child.communicate(timeout=3)
                record['error'] = 'Capture interrupted by experiment failure'
        (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        lines = ['## Python Launcher observation', '',
                 '**Job completion records observations. It does not prove the bank app opened or security checks passed.**', '',
                 '- Case: `{}`'.format(args.case), '- Result: `{}`'.format(report['result']),
                 '- Presentation mode: `{}`'.format(report['presentation_mode']),
                 '- File-binding reminder mode: `{}`; actual file associations are not changed.'.format(report['file_binding_reminder_mode']),
                 '- Normal app preference changed: `{}` (factoryKey `Python Script`, preference `with_terminal`).'.format(
                     report['launcher_presentation_preference']['changed']),
                 '- Architecture: `{}`'.format(report['architecture']),
                 '- Release SHA256: `{}`'.format(report.get('release_sha256', 'unavailable')),
                 '- Wrapper SHA256: `{}`'.format(report.get('wrapper_sha256', 'unavailable')), '',
                 'Download the artifact and inspect every launch-XXs.png before reporting the observed result.', '',
                 '### Limits', ''] + ['- '+s for s in LIMITS]
        if 'error' in report:
            lines += ['', 'Experiment error: '+report['error']]
        (out / 'summary.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
        print(json.dumps(dict(case=args.case, result=report['result'], error=report.get('error')), ensure_ascii=False))
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
