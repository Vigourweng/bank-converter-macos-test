"""Build a reproducible, allowlisted release with no statement data."""
from pathlib import Path
import stat
import zipfile

ROOT = Path(__file__).resolve().parent
APP_FILES = (
    'bank_merge.py', 'bank_formats.py', 'bank_mapping_ui.py',
    'start.cmd', 'start_mac.command', 'format_rules.json', 'README.md', '支持范围.txt',
    'vendor/LICENSE.xlrd',
)
XLRD_FILES = (
    '__init__.py', 'biffh.py', 'book.py', 'compdoc.py', 'formatting.py',
    'formula.py', 'info.py', 'sheet.py', 'timemachine.py', 'xldate.py',
)
ALLOWLIST = APP_FILES + tuple('vendor/xlrd/' + name for name in XLRD_FILES)


def build(output=None):
    output = Path(output) if output is not None else ROOT / 'dist' / 'bank-converter.zip'
    output.parent.mkdir(parents=True, exist_ok=True)
    for name in ALLOWLIST:
        if not (ROOT / name).is_file():
            raise FileNotFoundError('Required release file is missing: ' + name)
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in ALLOWLIST:
            entry = zipfile.ZipInfo('bank_converter/' + name, date_time=(2026, 1, 1, 0, 0, 0))
            entry.create_system = 3
            mode = 0o755 if name.endswith('.command') else 0o644
            entry.external_attr = (stat.S_IFREG | mode) << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, (ROOT / name).read_bytes())
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {'bank_converter/' + name for name in ALLOWLIST}
        assert archive.getinfo('bank_converter/start_mac.command').external_attr >> 16 & 0o111
    print(output)
    return output


if __name__ == '__main__':
    build()
