"""Per-user laser station installation from a verified, neutral school release.

Only the station runtime is copied. The school, queues and teacher authentication
remain on the central server. An atomic pointer selects runtime and configuration
together; the running station holds the same lease as the installer.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import uuid
from urllib.parse import urlsplit

from app.instance import InstanceLock

KIND = 'makervaerkstedet-laserstation'
CONNECTION_KIND = KIND + '-connection'
APP_FILES = tuple('app/' + name for name in (
    '__init__.py', 'laser_station.py', 'laser_station_install.py', 'megavaerkstedet.py',
    'laser_plate.py', 'laser_material.py', 'laser_scene.py', 'laser_ghost.py',
    'laser_transfer.py', 'laser_machines.py',
    'laser_presets_fbb2.json', 'laser_presets_fhexa1.json', 'instance.py', 'backup_guard.py'))
WEB_FILES = tuple('web/' + name for name in (
    'laserstation.html', 'laserstation.js', 'laserstation.css', 'laser.css',
    'megavaerkstedet.html', 'megavaerkstedet.js', 'megavaerkstedet.css',
    'favicon.svg', 'assets/metrophobic-latin.ttf', 'assets/Metrophobic-OFL.txt'))
LAUNCHER = 'deployment/school-setup/Open-LaserStation.ps1'
STOP_LAUNCHER = 'deployment/school-setup/Stop-LaserStation.ps1'
REQUIRED = set(APP_FILES + WEB_FILES + (LAUNCHER, STOP_LAUNCHER, 'runtime/python.exe',
                                      'CREDITS.md', 'LICENSE', 'LICENSE-SCOPE.md'))


def plain(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    if any(c in str(path) for c in '\r\n"'):
        raise ValueError('Stationsstier må ikke indeholde citationstegn eller linjeskift.')
    for item in (path, *path.parents):
        if item.is_symlink() or bool(getattr(item, 'is_junction', lambda: False)()):
            raise ValueError('Stationsstier må ikke indeholde links eller reparse points.')
    return path


def read_json(path):
    path = plain(path)
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError('Stationsmetadata er for store.')
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('Stationsmetadata skal være et JSON-objekt.')
    return value


def digest(path):
    with plain(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def atomic_json(path, value):
    temporary = path.with_name('.' + path.name + '-' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_connection(value):
    fields = {'schemaVersion', 'kind', 'school_name', 'server_url', 'caCertificatePem', 'station_id', 'machine'}
    if set(value) != fields or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1 or value['kind'] != CONNECTION_KIND:
        raise ValueError('Forbindelsesfilen er ikke en understøttet laserstationsfil.')
    name = value['school_name']
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or any(ord(c) < 32 for c in name):
        raise ValueError('Forbindelsesfilen mangler skolens navn.')
    url = value['server_url']
    if not isinstance(url, str) or any(c.isspace() or c in '\\<>"\'' for c in url):
        raise ValueError('Køserveren skal være en ren HTTPS-adresse.')
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment or not parsed.port):
        raise ValueError('Køserveren skal være en ren HTTPS-adresse med port.')
    if value['machine'] not in ('fbb2', 'fhexa1') or not isinstance(value['station_id'], str) or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?', value['station_id']):
        raise ValueError('Forbindelsesfilens station eller lasermodel er ugyldig.')
    pem = value['caCertificatePem']
    if not isinstance(pem, str) or len(pem) > 32 * 1024 or pem.count('-----BEGIN CERTIFICATE-----') != 1 or 'PRIVATE KEY' in pem:
        raise ValueError('Forbindelsesfilen må kun indeholde skolens offentlige CA-certifikat.')
    from cryptography import x509
    certificate = x509.load_pem_x509_certificate(pem.encode('ascii'))
    if not certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
        raise ValueError('Forbindelsesfilens certifikat er ikke et CA-certifikat.')
    now = datetime.now(timezone.utc)
    if not certificate.not_valid_before_utc <= now <= certificate.not_valid_after_utc:
        raise ValueError('Skolens CA-certifikat er ikke gyldigt på denne dato.')
    ssl.create_default_context(cadata=pem)
    return {**value, 'server_url': url.rstrip('/')}


def package_info(package):
    # Import only while installing from the complete school package. The station
    # deliberately does not ship server/update modules or a second school role.
    from app.release_compatibility import inspect_program
    release = inspect_program(package)
    marker = read_json(package / 'school-package.json')
    if marker.get('format') != 'elevhub-school-windows-v1' or marker.get('containsCredentials') is not False:
        raise ValueError('Brug en neutral, verificeret Makerværkstedet-skolepakke.')
    manifest = read_json(package / 'makerspace-release.json')
    files = manifest['files']
    if not REQUIRED <= files.keys():
        raise ValueError('Skolepakken mangler laserstationens program- eller startfiler.')
    selected = {name: record for name, record in files.items()
                if name in REQUIRED or name.startswith(('runtime/', 'licenses/'))}
    # The school-specific logo is optional in neutral distributions.
    logo = 'web/assets/klostermarksskolen-logo.png'
    if logo in files:
        selected[logo] = files[logo]
    return release, selected


def active_record(root):
    active = read_json(root / 'active.json')
    if (type(active.get('format')) is not int or active.get('format') != 1 or active.get('kind') != KIND
            or not re.fullmatch('[a-f0-9]{32}', str(active.get('installation_id', '')))
            or not re.fullmatch('versions/[a-f0-9]{32}', str(active.get('version_directory', '')))):
        raise ValueError('Ukendt installation; den historiske laserstation overskrives ikke.')
    return active


def active_installation(root):
    active = active_record(root)
    version = plain(root / active['version_directory'])
    config = read_json(version / 'laser-installation.json')
    if (config.get('role') != 'Station' or config.get('ca_file') != str(version / 'school-ca.pem')
            or not isinstance(config.get('plates'), str) or not Path(config['plates']).is_absolute()
            or type(config.get('port')) is not int or not 1024 <= config['port'] <= 65535):
        raise ValueError('Stationens konfiguration passer ikke til den valgte version.')
    connection = validate_connection(read_json(version / 'connection.json'))
    if (config.get('server') != connection['server_url'] or config.get('station_id') != connection['station_id']
            or config.get('machine') != connection['machine'] or (version / 'school-ca.pem').read_text(encoding='ascii') != connection['caCertificatePem']):
        raise ValueError('Stationens forbindelse eller identitet er ændret uden for installationen.')
    manifest = read_json(version / 'station-program.json')
    if manifest.get('source_manifest_sha256') != active.get('source_manifest_sha256') or manifest.get('release') != active.get('release'):
        raise ValueError('Den valgte stationsversion passer ikke til versionskvitteringen.')
    return active, version, config, connection


def verify_candidate(version, selected):
    program = version / 'program'
    for name, record in selected.items():
        path = plain(program / name)
        if path.stat().st_size != record['size'] or digest(path) != record['sha256']:
            raise ValueError('Stationskopiens kontrolsum er forkert: ' + name)


def probe_runtime(program):
    result = subprocess.run([str(program / 'runtime/python.exe'), '-B', '-c',
                             'import app.laser_station, app.laser_station_install'],
                            cwd=program, capture_output=True, text=True, timeout=45)
    if result.returncode:
        raise ValueError('Stationens kopierede runtime kunne ikke indlæses: ' + result.stderr[-2000:])


def assert_port_free(port):
    with socket.socket() as listener:
        try:
            listener.bind(('127.0.0.1', port))
        except OSError as error:
            raise ValueError('Luk den åbne laserstation før installation eller opdatering.') from error


def install(package, root, *, action, connection_file=None, plates=None, beam_studio=None,
            flux_api=None, port=8790, check_only=False):
    package, root = plain(package), plain(root)
    if action not in ('Install', 'Update', 'ResumeInstall'):
        raise ValueError('Vælg særskilt Install, Update eller ResumeInstall.')
    if package == root or package in root.parents or root in package.parents:
        raise ValueError('Pakken og stationens installationsmappe må ikke overlappe.')
    old = None
    pending = None
    if action == 'Install':
        if root.exists() and any(root.iterdir()):
            raise ValueError('Ren installation kræver en tom mappe. Brug Opdater laserstation for en kendt installation; en historisk eller afbrudt installation overskrives ikke.')
        if not connection_file:
            raise ValueError('Ren installation kræver skolens laser-station-connection.json.')
        connection = validate_connection(read_json(connection_file))
        plate_root = plain(plates or (Path.home() / 'Documents/Laserplader'))
        if plate_root.exists() and any(plate_root.iterdir()):
            raise ValueError('Ren installation kræver en tom plademappe; eksisterende plader overskrives ikke.')
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError('Stationsporten skal være mellem 1024 og 65535.')
        config = {'role': 'Station', 'server': connection['server_url'], 'plates': str(plate_root),
                  'port': port, 'machine': connection['machine'], 'station_id': connection['station_id']}
        for key, path in (('beam_studio', beam_studio), ('flux_api', flux_api)):
            if path:
                executable = plain(path)
                if not executable.is_file():
                    raise ValueError('Programfilen findes ikke: ' + str(executable))
                config[key] = str(executable)
    elif action == 'Update':
        if not (root / 'active.json').is_file():
            raise ValueError('Opdatering kræver en kendt separat station; historiske installationer overskrives ikke.')
        if any(value is not None for value in (connection_file, plates, beam_studio, flux_api)) or port != 8790:
            raise ValueError('Opdatering bevarer forbindelse, identitet, plademappe og maskinopsætning. Brug ikke nye installationsvalg.')
        old, _, config, connection = active_installation(root)
        plate_root = plain(config['plates'])
    else:
        if any(value is not None for value in (connection_file, plates, beam_studio, flux_api)) or port != 8790:
            raise ValueError('Genoptagelse bruger den journalførte forbindelse og opsætning.')
        pending = read_json(root / 'installation-pending.json')
        if (pending.get('kind') != KIND + '-pending' or pending.get('install_root') != str(root)
                or not re.fullmatch('[a-f0-9]{32}', str(pending.get('candidate_id', '')))
                or not re.fullmatch('[a-f0-9]{32}', str(pending.get('installation_id', '')))):
            raise ValueError('Genoptagelse kræver stationens egen installationsjournal.')
        connection = validate_connection(pending['connection'])
        config = pending['config']
        if (config.get('role') != 'Station' or config.get('server') != connection['server_url']
                or config.get('station_id') != connection['station_id'] or config.get('machine') != connection['machine']
                or type(config.get('port')) is not int or not 1024 <= config['port'] <= 65535
                or not isinstance(config.get('plates'), str) or not Path(config['plates']).is_absolute()):
            raise ValueError('Installationsjournalens stationsopsætning er ugyldig.')
        plate_root = plain(config['plates'])
    if plate_root == root or root in plate_root.parents or plate_root in root.parents:
        raise ValueError('Programmappe og plademappe skal være adskilte.')
    release, selected = package_info(package)
    if pending and pending.get('source_manifest_sha256') != release['manifest_sha256']:
        raise ValueError('Genoptagelse kræver præcis den journalførte udgivelse.')
    result = {'action': action, 'release': release['release'], 'manifest_sha256': release['manifest_sha256'],
              'server': config['server'], 'station_id': config['station_id'], 'plates': str(plate_root),
              'install_root': str(root), 'school_name': connection['school_name'], 'check_only': check_only}
    if check_only:
        return result
    root.mkdir(parents=True, exist_ok=True)
    version = None
    activated = False
    def cleanup_candidate():
        # Registered after the lifetime lease, so ExitStack calls this while
        # still owning that lease. ResumeInstall cannot reuse the candidate
        # between a failed copy and cleanup.
        if version is not None and not activated and version.exists() and action != 'ResumeInstall':
            safe = plain(version)
            active_path = root / 'active.json'
            if active_path.exists() and read_json(active_path).get('version_directory') == safe.relative_to(root).as_posix():
                return
            if safe.parent == root / 'versions':
                shutil.rmtree(safe)
    try:
        with ExitStack() as locks:
            locks.enter_context(InstanceLock(root, filename='station.lock'))
            locks.callback(cleanup_candidate)
            if action == 'Install':
                # Another installer may have passed the same empty-root
                # preflight. Only the lease holder may claim this directory.
                if any(item.name != 'station.lock' for item in root.iterdir()):
                    raise ValueError('Installationsmappen ændredes efter kontrollen; en eksisterende journal eller installation overskrives ikke.')
                pending = {'kind': KIND + '-pending', 'install_root': str(root), 'candidate_id': uuid.uuid4().hex,
                           'installation_id': uuid.uuid4().hex, 'source_manifest_sha256': release['manifest_sha256'],
                           'config': config, 'connection': connection}
                atomic_json(root / 'installation-pending.json', pending)
            elif action == 'ResumeInstall':
                if read_json(root / 'installation-pending.json') != pending:
                    raise ValueError('Installationsjournalen ændredes før låsen blev taget; start genoptagelsen igen.')
            if action == 'ResumeInstall' and (root / 'active.json').exists():
                current, selected_version, _, _ = active_installation(root)
                if current['installation_id'] != pending['installation_id'] or current['source_manifest_sha256'] != pending['source_manifest_sha256']:
                    raise ValueError('Den valgte installation matcher ikke genoptagelsesjournalen.')
                verify_candidate(selected_version, selected)
                (root / 'installation-pending.json').unlink()
                activated = True
                return {**result, 'installation_id': current['installation_id'], 'version_directory': str(selected_version)}
            if old:
                current = active_installation(root)[0]
                if current != old:
                    raise ValueError('Stationsversionen ændredes; start opdateringen igen.')
            elif (root / 'active.json').exists():
                raise ValueError('Stationen blev installeret af en anden proces.')
            if old and plate_root.exists():
                locks.enter_context(InstanceLock(plate_root))
            assert_port_free(config['port'])
            versions = plain(root / 'versions')
            versions.mkdir(exist_ok=True)
            candidate_id = pending['candidate_id'] if pending else uuid.uuid4().hex
            version = versions / ('.pending-' + candidate_id)
            final = versions / candidate_id
            if final.exists() and action == 'ResumeInstall':
                version = final
            if version.exists():
                allowed = {'program/' + name for name in selected} | {'school-ca.pem', 'connection.json', 'laser-installation.json', 'station-program.json'}
                for item in version.rglob('*'):
                    plain(item)
                    if item.is_file() and item.relative_to(version).as_posix() not in allowed:
                        raise ValueError('Den afbrudte stationsmappe indeholder ukendte filer; intet overskrives.')
            else:
                version.mkdir()
            program = version / 'program'
            for name in selected:
                target = program / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(package / name, target)
            verify_candidate(version, selected)
            probe_runtime(program)
            new_config = {**config, 'ca_file': str(final / 'school-ca.pem')}
            (version / 'school-ca.pem').write_text(connection['caCertificatePem'], encoding='ascii')
            atomic_json(version / 'connection.json', connection)
            atomic_json(version / 'laser-installation.json', new_config)
            atomic_json(version / 'station-program.json', {'release': release['release'],
                        'source_manifest_sha256': release['manifest_sha256'], 'files': selected})
            active = {'format': 1, 'kind': KIND, 'installation_id': old['installation_id'] if old else pending['installation_id'],
                      'version_directory': 'versions/' + candidate_id, 'release': release['release'],
                      'source_manifest_sha256': release['manifest_sha256'],
                      'activated_at': datetime.now(timezone.utc).isoformat()}
            if version != final:
                version.rename(final)
            version = final
            if old:
                history = plain(root / 'history')
                history.mkdir(exist_ok=True)
                atomic_json(history / (candidate_id + '-previous.json'), old)
            # The only activation point. Runtime, config, CA and version move together.
            atomic_json(root / 'active.json', active)
            activated = True
            if pending:
                (root / 'installation-pending.json').unlink(missing_ok=True)
            result.update({'installation_id': active['installation_id'], 'version_directory': str(final)})
        return result
    finally:
        # Never remove the shared root or lifetime lock here: another process
        # may have acquired that lock after this invocation released it. A
        # failed first install retains its journal for explicit ResumeInstall.
        pass


def running_descriptor(root):
    descriptor = read_json(root / 'running.json')
    if (set(descriptor) != {'format', 'kind', 'root', 'installation_id', 'version_directory', 'nonce'}
            or type(descriptor['format']) is not int or descriptor['format'] != 1 or descriptor['kind'] != KIND + '-running'
            or descriptor['root'] != str(root)
            or not re.fullmatch('[a-f0-9]{32}', str(descriptor['nonce']))
            or not re.fullmatch('[a-f0-9]{32}', str(descriptor['installation_id']))
            or not re.fullmatch('versions/[a-f0-9]{32}', str(descriptor['version_directory']))):
        raise ValueError('Stationskontrollen tilhører ikke denne installation.')
    return descriptor


def optional_running_descriptor(root):
    # Normal shutdown removes this file before releasing the lifetime lease.
    # Disappearance between stat/open is therefore expected, not a nonce swap.
    try:
        return running_descriptor(root)
    except FileNotFoundError:
        return None


def stop_request(root, descriptor):
    return root / ('stop-' + descriptor['nonce'] + '.json'), {
        'format': 1, 'kind': KIND + '-stop', 'root': str(root),
        'installation_id': descriptor['installation_id'], 'nonce': descriptor['nonce']}


def remove_own_control(root, descriptor):
    """Called only while holding the lifetime lease; never erase a new owner."""
    request_path, request = stop_request(root, descriptor)
    for path, expected in ((request_path, request), (root / 'running.json', descriptor)):
        if path.exists():
            try:
                matches = read_json(path) == expected
            except (OSError, ValueError):
                matches = False
            if matches:
                path.unlink()


def stop_station(root, *, timeout=15, expected_nonce=None):
    root = plain(root)
    # Stopping this local process must work even when its network CA expires
    # or its queue configuration becomes unreadable after startup.
    active = active_record(root)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < timeout <= 60:
        raise ValueError('Stop skal have en ventetid fra over 0 til højst 60 sekunder.')
    descriptor = optional_running_descriptor(root)
    if descriptor is None:
        if expected_nonce is not None:
            raise ValueError('Stopanmodningen matcher ingen kørende stationsinstans.')
        try:
            with InstanceLock(root, filename='station.lock'):
                return {'stopped': True, 'already_stopped': True, 'installation_id': active['installation_id']}
        except RuntimeError:
            raise ValueError('Stationen er ved at starte eller blive opdateret. Prøv Stop laserstation igen.') from None
    if (descriptor['installation_id'] != active['installation_id']
            or descriptor['version_directory'] != active['version_directory']
            or expected_nonce is not None and descriptor['nonce'] != expected_nonce):
        raise ValueError('Stopanmodningen matcher ikke den aktuelle stationsinstans.')
    try:
        probe = InstanceLock(root, filename='station.lock')
    except RuntimeError:
        probe = None
    if probe is not None:
        probe.close()
        raise ValueError('Stationskontrollen er forældet; ingen kørende instans ejer låsen.')
    current = optional_running_descriptor(root)
    if current is not None and current != descriptor:
        raise ValueError('Stationsinstansen skiftede før stopanmodningen.')
    request_path, request = stop_request(root, descriptor)
    try:
        with plain(request_path).open('x', encoding='utf-8') as stream:
            json.dump(request, stream)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        if read_json(request_path) != request:
            raise ValueError('En anden lokal stopanmodning findes; den overskrives ikke.') from None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = optional_running_descriptor(root)
        if current is not None and current != descriptor:
            raise ValueError('Stationsinstansen skiftede; den nye instans bliver ikke stoppet.')
        try:
            lease = InstanceLock(root, filename='station.lock')
        except RuntimeError:
            time.sleep(0.05)
            continue
        with lease:
            current = optional_running_descriptor(root)
            if current is not None and current != descriptor:
                raise ValueError('Stationsinstansen skiftede; den nye instans bliver ikke stoppet.')
            remove_own_control(root, descriptor)
            return {'stopped': True, 'already_stopped': False, 'installation_id': active['installation_id'],
                    'nonce': descriptor['nonce']}
    raise TimeoutError('Laserstationen er endnu ikke stoppet. Afvent igangværende arbejde og prøv igen; ingen proces er tvangsafsluttet.')


def run_station(root):
    root = plain(root)
    with InstanceLock(root, filename='station.lock'):
        active, version, _, _ = active_installation(root)
        expected = version / 'program'
        if plain(Path(__file__).parents[1]) != expected:
            raise ValueError('Stationsversionen ændredes under start. Åbn genvejen igen.')
        manifest = read_json(version / 'station-program.json')
        verify_candidate(version, manifest['files'])
        if (root / 'running.json').exists():
            previous = running_descriptor(root)
            if previous['installation_id'] != active['installation_id']:
                raise ValueError('En anden installations stationskontrol overskrives ikke.')
            remove_own_control(root, previous)
        descriptor = {'format': 1, 'kind': KIND + '-running', 'root': str(root),
                      'installation_id': active['installation_id'], 'version_directory': active['version_directory'],
                      'nonce': uuid.uuid4().hex}
        atomic_json(root / 'running.json', descriptor)
        requested = threading.Event()
        finished = threading.Event()
        request_path, request = stop_request(root, descriptor)
        def watch_request():
            while not finished.wait(0.1):
                if request_path.exists():
                    try:
                        matches = read_json(request_path) == request
                    except (OSError, ValueError):
                        matches = False
                    if matches:
                        requested.set()
                        return
        watcher = threading.Thread(target=watch_request, name='station-local-control', daemon=True)
        watcher.start()
        try:
            from app.laser_station import main as station_main
            station_main(['--config', str(version / 'laser-installation.json')], stop_event=requested)
        finally:
            finished.set()
            watcher.join(timeout=2)
            remove_own_control(root, descriptor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    installer = commands.add_parser('install')
    installer.add_argument('--package-root', type=Path, required=True)
    installer.add_argument('--install-root', type=Path, required=True)
    installer.add_argument('--action', choices=('Install', 'Update', 'ResumeInstall'), required=True)
    installer.add_argument('--connection-file', type=Path)
    installer.add_argument('--plates', type=Path)
    installer.add_argument('--beam-studio', type=Path)
    installer.add_argument('--flux-api', type=Path)
    installer.add_argument('--port', type=int, default=8790)
    installer.add_argument('--check-only', action='store_true')
    for command in ('run', 'info'):
        child = commands.add_parser(command)
        child.add_argument('--install-root', type=Path, required=True)
    stop = commands.add_parser('stop')
    stop.add_argument('--install-root', type=Path, required=True)
    stop.add_argument('--timeout', type=float, default=15)
    stop.add_argument('--nonce')
    args = parser.parse_args()
    if args.command == 'run':
        run_station(args.install_root)
    elif args.command == 'stop':
        print(json.dumps(stop_station(args.install_root, timeout=args.timeout, expected_nonce=args.nonce)))
    elif args.command == 'info':
        active, version, config, connection = active_installation(plain(args.install_root))
        print(json.dumps({'active': active, 'version': str(version), 'config': config, 'connection': connection}, ensure_ascii=False))
    else:
        print(json.dumps(install(args.package_root, args.install_root, action=args.action,
              connection_file=args.connection_file, plates=args.plates, beam_studio=args.beam_studio,
              flux_api=args.flux_api, port=args.port, check_only=args.check_only), ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
