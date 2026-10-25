"""Install/verify/launch official Lark on an empty hosted Mac, without login.

No screenshots, profile logs, account credentials, QR codes, cookies, or user
documents are collected. This preflight cannot complete the Lark download path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request


URL = 'https://sf16-sg.larksuitecdn.com/obj/lark-version-sg/bca1e6e3/Lark-darwin_arm64-8.3.14-signed.dmg'
SHA256 = '4a6ee770406f323d1a9250d9a1c84352c159ce37dd342856c6605327e3525aa6'
APP_PATH = Path('/Applications/LarkSuite.app')


def command(argv, timeout=60):
    try:
        result = subprocess.run([str(x) for x in argv], stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=timeout)
        return {'returncode':result.returncode,
                'stdout':result.stdout.decode('utf-8', errors='replace')[:32768],
                'stderr':result.stderr.decode('utf-8', errors='replace')[:32768]}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {'returncode':None, 'error':str(error)}


def require(result, label):
    if result['returncode'] != 0:
        raise RuntimeError(label + ': ' + json.dumps(result, ensure_ascii=False))


def file_sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda:handle.read(4*1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def bundle_metadata(app):
    info = plistlib.loads((app/'Contents'/'Info.plist').read_bytes())
    keys = ('CFBundleIdentifier', 'CFBundleName', 'CFBundleDisplayName',
            'CFBundleExecutable', 'CFBundleVersion', 'CFBundleShortVersionString',
            'LSMinimumSystemVersion', 'LSFileQuarantineEnabled')
    metadata = {key:info.get(key) for key in keys}
    metadata['has_Mac_App_Store_receipt'] = (app/'Contents'/'_MASReceipt'/'receipt').is_file()
    return metadata


def signatures(app):
    verification = command(['/usr/bin/codesign','--verify','--deep','--strict','--verbose=2',app], timeout=90)
    detail = command(['/usr/bin/codesign','-dv','--verbose=4',app])
    assessment = command(['/usr/sbin/spctl','--assess','--type','execute','-vv',app], timeout=90)
    raw = detail.get('stdout','')+'\n'+detail.get('stderr','')
    public_detail = [line for line in raw.splitlines() if line.startswith(
        ('Authority=','TeamIdentifier=','Identifier=','CDHash=','Timestamp=','Runtime Version='))]
    return {'verification':verification, 'publisher_metadata':public_detail,
            'detail_returncode':detail['returncode'], 'assessment':assessment}


def entitlements(app):
    result = command(['/usr/bin/codesign','-d','--entitlements',':-',app])
    # Record only signed entitlement booleans relevant to file downloads. Never
    # read Lark's user profile or run-time logs.
    text = result.get('stdout','')+'\n'+result.get('stderr','')
    begin = text.find('<?xml')
    end = text.find('</plist>', begin)
    values = {}
    if begin >= 0 and end > begin:
        try:
            values = plistlib.loads(text[begin:end+8].encode('utf-8'))
        except Exception:
            pass
    keys = ('com.apple.security.app-sandbox', 'com.apple.security.network.client',
            'com.apple.security.files.user-selected.read-write',
            'com.apple.security.files.downloads.read-write')
    return {'command_returncode':result['returncode'], 'parsed':bool(values),
            'selected_signed_entitlements':{key:values.get(key) for key in keys}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts',type=Path,required=True)
    args = parser.parse_args()
    if (sys.platform != 'darwin' or platform.machine() != 'arm64'
            or os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('BANK_LARK_RUNNER_KIND') != 'github-hosted'
            or os.environ.get('RUNNER_OS') != 'macOS'):
        parser.error('Refusing: only an empty disposable GitHub-hosted Apple Silicon Mac is supported.')
    destination = args.artifacts.resolve()
    destination.mkdir(parents=True,exist_ok=False)
    report = {
        'experiment':'Official Lark Mac preflight without login',
        'actual_macos':True,'architecture':platform.machine(),'platform':platform.platform(),
        'installer_url':URL,'expected_installer_sha256':SHA256,
        'installer_hash_source':'https://formulae.brew.sh/api/cask/lark.json (snapshot generated 2026-10-22)',
        'distribution_tested':'official website DMG, Apple Silicon, pinned 8.3.14',
        'affected_Mac_distribution':'unknown (user could not identify App Store versus website)',
        'auth_status':'NOT_ATTEMPTED_NO_TEST_ACCOUNT',
        'full_repro_status':'incomplete_no_authenticated_Lark_download',
        'stages':{'install':'NOT_RUN','trusted_launch':'NOT_RUN','account_login':'NOT_RUN',
                  'download_ZIP_through_Lark':'NOT_RUN','Finder_extract_downloaded_ZIP':'NOT_RUN',
                  'Finder_open_start_mac_command':'NOT_RUN'},
        'captures':'No screenshots, login QR codes, credentials, cookies, app profile or runtime logs collected.',
        'result':'preflight_incomplete',
    }
    mount = destination/'readonly-dmg'
    mounted = False
    code = 1
    try:
        if APP_PATH.exists() or APP_PATH.is_symlink():
            raise RuntimeError('Lark already exists; refusing to replace an app or use an existing profile.')
        gatekeeper = command(['/usr/sbin/spctl','--status'])
        report['gatekeeper_status'] = gatekeeper
        require(gatekeeper,'Gatekeeper status')
        if 'assessments enabled' not in gatekeeper.get('stdout',''):
            raise RuntimeError('Gatekeeper is not enabled; refusing to change settings or launch.')
        installer = destination/'official-Lark-darwin_arm64-8.3.14-signed.dmg'
        request = urllib.request.Request(URL,headers={'User-Agent':'BankToolLarkPreflight/1.0'})
        with urllib.request.urlopen(request,timeout=60) as response, installer.open('xb') as handle:
            if urllib.parse.urlsplit(response.url).hostname not in {'sf16-sg.larksuitecdn.com','lf16-larkversion-new-sign.larksuitecdn.com'}:
                raise RuntimeError('Unexpected installer redirect host')
            while block := response.read(4*1024*1024):
                handle.write(block)
        report['actual_installer_sha256'] = file_sha(installer)
        assert report['actual_installer_sha256'] == SHA256, 'Official download differs from registry checksum'
        report['installer_bytes'] = installer.stat().st_size
        mount.mkdir()
        # Do not pipe yes, use accept-license flags, or accept any license UI.
        # A license that blocks hdiutil makes this preflight stop honestly.
        attachment = command(['/usr/bin/hdiutil','attach','-readonly','-nobrowse','-noautoopen',
                              '-mountpoint',mount,'-plist',installer], timeout=90)
        require(attachment,'Read-only DMG mount (no license acceptance is automated)')
        mounted = True
        source_app = mount/'LarkSuite.app'
        if not source_app.is_dir() or source_app.is_symlink():
            raise RuntimeError('Expected official LarkSuite.app was not found at the DMG root')
        source_meta = bundle_metadata(source_app)
        source_signatures = signatures(source_app)
        report['source_bundle_metadata'] = source_meta
        report['source_signatures'] = source_signatures
        require(source_signatures['verification'],'Vendor app strict signature verification')
        require(source_signatures['assessment'],'Vendor app Gatekeeper assessment')
        if source_signatures['detail_returncode'] != 0 or not any(
                line.startswith('Authority=Developer ID Application:')
                for line in source_signatures['publisher_metadata']):
            raise RuntimeError('Expected a valid Developer ID Application publisher before launch')
        copying = command(['/usr/bin/ditto',source_app,APP_PATH],timeout=120)
        require(copying,'Copy verified official app to empty Applications location')
        report['stages']['install'] = 'COMPLETED_OFFICIAL_WEBSITE_APP'
        report['installed_bundle_metadata'] = bundle_metadata(APP_PATH)
        installed_signatures = signatures(APP_PATH)
        report['installed_signatures'] = installed_signatures
        require(installed_signatures['verification'],'Installed app strict signature verification')
        require(installed_signatures['assessment'],'Installed app Gatekeeper assessment')
        report['signed_download_entitlements'] = entitlements(APP_PATH)
        name = report['installed_bundle_metadata']['CFBundleExecutable']
        if not isinstance(name,str) or Path(name).name != name:
            raise RuntimeError('Unexpected bundle executable name')
        executable = (APP_PATH/'Contents'/'MacOS'/name).resolve(strict=True)
        if APP_PATH.resolve() not in executable.parents:
            raise RuntimeError('Executable is outside the installed app bundle')
        # Launch only the verified, accepted vendor app. Do not automate its
        # account, terms, QR login, message, download or privacy interfaces.
        launch = command(['/usr/bin/open','-a',APP_PATH],timeout=20)
        report['launch_request'] = {'returncode':launch['returncode'],
                                    'error':launch.get('error')}
        require(launch,'Launch verified Lark')
        pids = []
        for delay in (1,2,3):
            time.sleep(delay)
            probe = command(['/usr/bin/pgrep','-f',re.escape(str(executable))],timeout=10)
            pids = [int(line) for line in probe.get('stdout','').splitlines() if line.isdigit()]
            if pids:
                break
        report['process_evidence'] = {'bundle_executable':name,'running_pids':pids,
                                      'recorded_process_arguments':False}
        if not pids:
            raise RuntimeError('Lark process was not observed; no screenshot or app log will be captured')
        report['stages']['trusted_launch'] = 'PROCESS_OBSERVED_UI_NOT_INSPECTED'
        report['result'] = 'ready_without_login'
        report['next_required_step'] = ('A separately authorized test account and private interactive session are required '
            'to download the exact ZIP through Lark. No such account is available; full reproduction remains incomplete.')
        code = 0
    except Exception as error:
        report['result'] = 'preflight_blocked'
        report['error'] = f'{type(error).__name__}: {error}'
    finally:
        if mounted:
            report['readonly_dmg_detach'] = command(['/usr/bin/hdiutil','detach',mount],timeout=30)
        # The downloaded DMG is large and is NOT included in uploaded artifacts.
        # Move public metadata into a dedicated allowlisted artifact subfolder.
        metadata_dir = destination/'report-only'
        metadata_dir.mkdir()
        (metadata_dir/'lark-preflight-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        summary = ['## Official Lark Mac preflight', '',
                   '**This is not a completed Lark download reproduction.**', '',
                   f"- Result: `{report['result']}`", '- Variant: official website ARM64 DMG, pinned8.3.14',
                   f"- Installer SHA256: `{report.get('actual_installer_sha256','not verified')}`",
                   '- Login: not attempted; no test account is available.',
                   '- ZIP download through Lark: NOT RUN.', '- Finder extraction: NOT RUN.',
                   '- Finder opening the downloaded command: NOT RUN.',
                   '- No login QR images, screenshots, account cookies, credentials, profile files or app runtime logs are collected.',
                   '- The affected Mac\'s Lark distribution is unknown; this website build is not asserted to match an App Store build.',
                   '', 'Download the JSON report for static bundle/publisher/entitlement metadata and process-only launch evidence.']
        if 'error' in report:
            summary += ['', 'Preflight stopped: `' + report['error'].replace('`',"'") + '`']
        (metadata_dir/'summary.md').write_text('\n'.join(summary)+'\n',encoding='utf-8')
        print(json.dumps({'result':report['result'],'full_repro_status':report['full_repro_status'],
                          'error':report.get('error')},ensure_ascii=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
