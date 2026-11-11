"""Read every ZIP member and verify it against the installer integrity manifest."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile
import sys

ROOT = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser()
parser.add_argument('--school', action='store_true', help='Verify the private Klostermarksskolen edition.')
args = parser.parse_args()
name = 'Printvaerksted-Klostermarksskolen-0.4.8' if args.school else 'Printvaerksted-Windows-0.4.8'
archive_path = ROOT / 'dist' / (name + '.zip')
prefix = name + '/'
transfer_name = 'Skolens-printere-privat.json'
with zipfile.ZipFile(archive_path) as archive:
    members = archive.namelist()
    integrity = json.loads(archive.read(prefix + 'integrity.json'))
    assert len(members) == len(set(members)), 'Duplicate archive members'
    assert set(members) == {prefix + name for name in integrity} | {prefix + 'integrity.json'}
    for index, (name, expected) in enumerate(integrity.items(), 1):
        parts = Path(name).parts
        assert '..' not in parts and parts[0] not in ('data', '.runtime', 'setup')
        assert Path(name).name not in ('id_ed25519', 'id_ed25519.pub')
        if Path(name).name == transfer_name:
            assert args.school and name == transfer_name
        with archive.open(prefix + name) as source:
            actual = hashlib.file_digest(source, 'sha256').hexdigest()
        assert actual == expected, f'Checksum mismatch: {name}'
        if index % 1500 == 0:
            print(f'Verified {index}/{len(integrity)} files...', flush=True)
    for folder in ('app', 'web'):
        for path in (ROOT / folder).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
                relative = path.relative_to(ROOT).as_posix()
                assert archive.read(prefix + relative) == path.read_bytes(), relative
    marker = json.loads(archive.read(prefix + 'printvaerksted-package.json'))
    assert (transfer_name in integrity) == args.school
    printer_names = []
    if args.school:
        sys.path.insert(0, str(ROOT))
        from app.installation import read_printer_transfer
        expected_printers, expected_pins = read_printer_transfer(ROOT / 'dist' / transfer_name)
        transfer = json.loads(archive.read(prefix + transfer_name))
        from app.camera_transfer import read_camera_transfer
        expected = {'format': 'printvaerksted-printers-v1', 'printers': expected_printers, 'certificates': expected_pins}
        cameras = read_camera_transfer(json.loads((ROOT / 'dist' / transfer_name).read_text(encoding='utf-8')), expected_printers)
        if cameras:
            expected['cameras'] = cameras
        assert transfer == expected
        assert len(expected_printers) == marker['configured_printers'] == 4
        assert all(p['model'] == 'A1 mini' and p['enabled'] is False for p in expected_printers)
        assert [p['name'] for p in expected_printers] == ['E1', 'E2', 'E3', 'E4']
        assert marker['new_queue_paused'] and marker['imported_printers_enabled'] and marker['activate_imported_printers']
        assert marker['start_file'] == 'START.cmd' and 'START.cmd' in integrity
        printer_names = [p['name'] for p in expected_printers]
    assert marker['version'] == '0.4.8'
    assert marker['maintenance_interval'] == 'calendar-month'
    assert 'web/maintenance.js' in integrity
    assert marker['firmware_check_interval_seconds'] == 604800
    assert 'app/firmware.py' in integrity and 'web/firmware.js' in integrity
    assert marker['ai_background_interval_seconds'] == 600 and marker['ai_unload_after_check']
    assert marker['camera_auto_enabled'] == (args.school and any(c['settings']['automatic'] for c in cameras.values()))
    assert 'id="setup-section"' not in archive.read(prefix + 'web/index.html').decode('utf-8')
    result = {'archive': str(archive_path), 'bytes': archive_path.stat().st_size,
              'sha256_verified_files': len(integrity), 'zip_crc': 'passed',
              'exact_current_app_and_web_source': True, 'printer_settings_included': args.school,
              'printers': printer_names, 'no_queue_or_account_files': True, 'camera_references_included': marker.get('camera_references_included', False), 'components': marker}
    report_path = ROOT / '.runtime' / ('package-verification-0.4.8-school.json' if args.school else 'package-verification-0.4.8.json')
    report = json.loads(report_path.read_text()) if report_path.exists() else {'version': '0.4.8'}
    report['archive'] = result
    report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(result), flush=True)
