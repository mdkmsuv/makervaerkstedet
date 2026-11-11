"""Offline package checks and optional printer migration. Never contacts printers."""
import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import shutil
from .paths import data_root

ROOT = Path(__file__).resolve().parent.parent


def verify_files(root):
    root = Path(root).resolve()
    manifest = json.loads((root / 'integrity.json').read_text(encoding='utf-8'))
    for name, expected in manifest.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('Pakken er ufuldstændig. Udpak hele ZIP-filen igen.')
        with path.open('rb') as source:
            actual = hashlib.file_digest(source, 'sha256').hexdigest()
        if actual != expected:
            raise ValueError(f'Filen {name} er beskadiget. Udpak hele ZIP-filen igen.')
    return len(manifest)


def read_printer_transfer(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('format') != 'printvaerksted-printers-v1' or not isinstance(data.get('printers'), list):
        raise ValueError('Filen er ikke en printerflyttefil fra Printværksted.')
    printers, ids, hosts, serials = [], set(), set(), set()
    for original in data['printers']:
        p = {key: original[key] for key in ('id', 'name', 'model', 'host', 'serial', 'access_code', 'bed_type')}
        p['enabled'] = False
        if not re.fullmatch('[0-9a-f]{32}', p['id']) or p['model'] not in ('A1 mini', 'P1S', 'H2D'):
            raise ValueError('Printerflyttefilen indeholder en ugyldig printer.')
        address = ipaddress.ip_address(p['host'])
        if address.version != 4 or not address.is_private or address.is_loopback or address.is_multicast or address.is_unspecified:
            raise ValueError('Printerflyttefilen indeholder en ugyldig printeradresse.')
        if not p['serial'].isalnum() or len(p['serial']) > 30 or not p['access_code'] or len(p['access_code']) > 100:
            raise ValueError('Printerflyttefilen indeholder ugyldige forbindelsesoplysninger.')
        if not isinstance(p['name'], str) or not p['name'] or len(p['name']) > 50 or p['bed_type'] not in ('Textured PEI Plate', 'High Temp Plate', 'Supertack Plate'):
            raise ValueError('Printerflyttefilen indeholder ugyldige printerindstillinger.')
        if p['id'] in ids or p['host'] in hosts or p['serial'] in serials:
            raise ValueError('En printer er med flere gange i flyttefilen.')
        ids.add(p['id']); hosts.add(p['host']); serials.add(p['serial'])
        printers.append(p)
    pins = data.get('certificates', {})
    for name, value in pins.items():
        match = re.fullmatch(r'([0-9a-f]{32})-(camera|mqtt|ftp)\.sha256', name)
        if not match or match[1] not in ids or not re.fullmatch('[0-9a-f]{64}', value):
            raise ValueError('Printerflyttefilen indeholder en ugyldig printeridentitet.')
    return printers, pins


def configure(root, transfer=None):
    from .core import Queue
    from .camera_transfer import read_camera_transfer, import_cameras
    root = Path(root).resolve()
    folder = data_root(root) / 'school'
    folder.mkdir(parents=True, exist_ok=True)
    settings_path = folder / 'settings.json'
    existed = settings_path.exists()
    settings = json.loads(settings_path.read_text(encoding='utf-8')) if existed else {'printers': []}
    imported = False
    pins = {}
    cameras = {}
    if transfer and not settings.get('printers'):
        settings['printers'], pins = read_printer_transfer(transfer)
        cameras = read_camera_transfer(json.loads(Path(transfer).read_text(encoding='utf-8')), settings['printers'])
        marker_path = root / 'printvaerksted-package.json'
        marker = json.loads(marker_path.read_text(encoding='utf-8')) if marker_path.exists() else {}
        if marker.get('distribution') == 'klostermarksskolen' and marker.get('activate_imported_printers') is True:
            # This commissioned school package enables its saved connections.
            # The new queue is still paused, has no jobs and no cleared beds.
            for printer in settings['printers']:
                printer['enabled'] = True
        imported = True
    # All installations use the included, tested slicer regardless of drive letter.
    settings['studio_path'] = str(root / 'tools' / 'bambu' / 'bambu-studio.exe')
    # Import before committing the fresh printer settings, so interruption can
    # resume missing folders. Upgrades never replace their existing camera setup.
    camera_count = import_cameras(folder, cameras)
    temporary = settings_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(settings_path)
    if imported:
        certs = folder / 'certificates'
        certs.mkdir(exist_ok=True)
        for name, value in pins.items():
            (certs / name).write_text(value, encoding='ascii')
    if not existed or imported:
        queue = Queue(folder, settings['printers'])
        try:
            queue.pause(True)
        finally:
            queue.close()
    return {'printers': len(settings['printers']), 'imported': imported, 'cameras_imported': camera_count, 'existing_data_preserved': existed and not imported}


def redirect_legacy(source, destination):
    """Keep older desktop shortcuts attached to the migrated queue, not a copy."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination:
        return
    for root in (source, destination):
        if json.loads((root / 'printvaerksted-package.json').read_text())['app'] != 'printvaerksted':
            raise ValueError('Flytning kræver to Printværksted-installationer.')
    for module in ('desktop', 'server'):
        path = source / 'app' / (module + '.py')
        backup = path.with_suffix('.py.before-autostart')
        if not backup.exists():
            shutil.copy2(path, backup)
        # Literal paths, with no shell interpretation or lookup of user-writable code.
        args = "sys.argv[1:]" if module == 'desktop' else "['--no-window']"
        code = ('"""Forward old shortcuts to the migrated installation."""\n'
                'import subprocess, sys\n'
                'if __name__ == "__main__":\n'
                f'    result = subprocess.run([{str(destination / "runtime" / "python.exe")!r}, "-m", "app.desktop"] + {args}, '
                f'cwd={str(destination)!r}, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))\n'
                '    sys.exit(result.returncode)\n')
        temporary = path.with_suffix('.migration-tmp')
        temporary.write_text(code, encoding='utf-8')
        temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('verify-files', 'configure', 'migrate-data', 'redirect-legacy'))
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--printer-settings', type=Path)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--destination', type=Path)
    args = parser.parse_args()
    if args.action == 'verify-files':
        print(json.dumps({'verified_files': verify_files(args.root)}))
    elif args.action == 'configure':
        print(json.dumps(configure(args.root, args.printer_settings)))
    elif args.action == 'redirect-legacy':
        redirect_legacy(args.source, args.root)
    elif args.action == 'migrate-data':
        if not args.source or not args.destination or not (args.source / 'school').is_dir():
            raise ValueError('Datamappen fra den gamle installation mangler.')
        if (args.destination / 'school' / 'queue.sqlite3').exists():
            raise ValueError('En eksisterende kø må ikke overskrives under flytning.')
        shutil.copytree(args.source, args.destination, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('instance.lock', 'supervisor', 'installation.log'))
        print(json.dumps({'migrated': True, 'original_preserved': True}))


if __name__ == '__main__':
    main()
