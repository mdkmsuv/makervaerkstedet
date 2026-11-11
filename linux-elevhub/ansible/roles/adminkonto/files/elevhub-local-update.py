#!/usr/bin/python3
"""Fixed, root-owned actions for the authenticated local admin update screen."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import urllib.error
from types import SimpleNamespace

CONFIG_DIR = Path('/etc/elevhub')
KIOSK = CONFIG_DIR / 'kiosk.json'
DEVICE = CONFIG_DIR / 'enhed.json'
ADMIN = CONFIG_DIR / 'admin-login.json'
RELEASE = Path('/etc/elevhub/release.json')
CLIENT = Path('/usr/local/lib/elevhub/elevhub_klient.py')
STATE_DIR = Path('/var/lib/elevhub/local-update')
APPROVAL = STATE_DIR / 'approval.json'
REQUEST = STATE_DIR / 'request.json'
UPDATE_ROOT = Path('/var/lib/elevhub/updates')
HEARTBEAT = Path('/run/elevhub-update-status.json')
HELPER_ACTIONS = {'state', 'check', 'update', 'restart', 'retry'}
VERSION = re.compile(
    r'^(?P<core>[0-9]+(?:\.[0-9]+){1,3})'
    r'(?:-(?P<pre>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?'
    r'(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$')
SHA256 = re.compile(r'^[a-f0-9]{64}$')
COMPUTER = re.compile(r'^[a-z0-9][a-z0-9-]{0,31}$')
REQUEST_ID = re.compile(r'^[a-f0-9]{32}$')
MAX_JSON = 65536
MAX_PINS_BYTES = 16 * 1024 * 1024
MAX_INSTALLER_BYTES = 1024 * 1024


class UpdateError(RuntimeError):
    """A safe-to-display update failure."""


class ServerRejected(UpdateError):
    """The controller returned an explicit client error for an update command."""


class Paths(SimpleNamespace):
    @classmethod
    def for_test(cls, root):
        root = Path(root)
        return cls(
            kiosk=root / 'etc/elevhub/kiosk.json',
            device=root / 'etc/elevhub/enhed.json',
            admin=root / 'etc/elevhub/admin-login.json',
            release=root / 'etc/elevhub/release.json',
            client=root / 'usr/local/lib/elevhub/elevhub_klient.py',
            state_dir=root / 'var/lib/elevhub/local-update',
            approval=root / 'var/lib/elevhub/local-update/approval.json',
            request=root / 'var/lib/elevhub/local-update/request.json',
            update_root=root / 'var/lib/elevhub/updates',
            heartbeat=root / 'run/elevhub-update-status.json',
            ca=root / 'etc/elevhub/ca.pem')


PRODUCTION_PATHS = Paths(kiosk=KIOSK, device=DEVICE, admin=ADMIN, release=RELEASE,
                         client=CLIENT, state_dir=STATE_DIR, approval=APPROVAL,
                         request=REQUEST, update_root=UPDATE_ROOT, heartbeat=HEARTBEAT,
                         ca=Path('/etc/elevhub/ca.pem'))


def parse_action(argv):
    if len(argv) != 1 or argv[0] not in HELPER_ACTIONS:
        raise ValueError('Brug en af de faste handlinger: state, check, update, restart, retry.')
    return argv[0]


def _read_json(path, *, max_bytes=MAX_JSON):
    try:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
            raise UpdateError('En lokal opdateringsfil er ugyldig.')
        if os.name != 'nt' and (info.st_uid != 0 or info.st_mode & 0o022):
            raise UpdateError('En lokal opdateringsfil har usikkert ejerskab.')
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except UpdateError:
        raise
    except (OSError, ValueError, UnicodeError, TypeError) as exc:
        raise UpdateError('En lokal opdateringsfil kunne ikke læses.') from exc


def _read_private_json(path, *, max_bytes=MAX_JSON):
    if not _safe_root_directory(Path(path).parent, private=True):
        raise UpdateError('Den lokale opdateringstilstand har usikre rettigheder.')
    if _safe_root_file(path, max_bytes=max_bytes, private=True) is None:
        raise UpdateError('Den lokale opdateringstilstand har usikre rettigheder.')
    return _read_json(path, max_bytes=max_bytes)


def write_json(path, value, *, mode=0o600):
    """Atomic write; root-only in production, portable for synthetic tests."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != 'nt':
        os.chmod(path.parent, 0o700)
    temporary = path.with_name(path.name + '.tmp-' + secrets.token_hex(4))
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != 'nt':
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _validate_local_config(paths):
    kiosk = _read_json(paths.kiosk)
    device = _read_json(paths.device)
    admin = _read_json(paths.admin)
    if not isinstance(kiosk, dict) or not isinstance(device, dict) or not isinstance(admin, dict):
        raise UpdateError('Enhedens lokale opdateringsindstillinger er ugyldige.')
    computer = kiosk.get('computer')
    if not isinstance(computer, str) or not COMPUTER.fullmatch(computer):
        raise UpdateError('Enhedens navn mangler i lokale indstillinger.')
    if device.get('computer') != computer:
        raise UpdateError('Enhedens identitet stemmer ikke med lokale indstillinger.')
    # The installed kiosk role stores the root credential under this exact key.
    token = device.get('token')
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43,128}', token):
        raise UpdateError('Enhedens autentificering mangler.')
    username = admin.get('username')
    if not isinstance(username, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', username):
        raise UpdateError('Den lokale administratorkonto er ikke sat op.')
    hub = kiosk.get('hub')
    ca_file = kiosk.get('ca_fil')
    if not isinstance(hub, str) or not isinstance(ca_file, str) or not ca_file:
        raise UpdateError('Forbindelsen til Elevhub er ikke sat op.')
    return kiosk, computer, token, username, ca_file


def _client_module(client_path):
    spec = importlib.util.spec_from_file_location('elevhub_local_update_client', client_path)
    if spec is None or spec.loader is None:
        raise UpdateError('Elevhub-klienten kunne ikke indlæses.')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _request(paths, client, *, data=None):
    kiosk, computer, token, _username, ca_file = _validate_local_config(paths)
    try:
        return client.hent_json(kiosk['hub'], '/api/enhed/opdatering',
                                token=token, computer=computer, ca_fil=ca_file,
                                data=data, timeout=15)
    except urllib.error.HTTPError as exc:
        if 400 <= exc.code < 500:
            raise ServerRejected(
                'Elevhub afviste opdateringen. Det tidligere valg er bevaret.') from exc
        raise UpdateError(
            'Kunne ikke hente opdateringsstatus fra Elevhub. Kontrollér netværk og prøv igen.') from exc
    except Exception as exc:
        # Never relay transport exceptions: some libraries include headers or URLs.
        raise UpdateError('Kunne ikke hente opdateringsstatus fra Elevhub. Kontrollér netværk og prøv igen.') from exc


def _version_key(value):
    match = VERSION.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise UpdateError('Elevhub sendte et ugyldigt versionsnummer.')
    parts = tuple(int(part) for part in match.group('core').split('.'))
    core = parts + (0,) * (4 - len(parts))
    prerelease = match.group('pre')
    if prerelease is None:
        return core, 1, ()
    identifiers = tuple((0, int(item)) if item.isdigit() else (1, item)
                        for item in prerelease.split('.'))
    return core, 0, identifiers


def _validate_state(value, computer):
    if not isinstance(value, dict) or value.get('schema') != 1 or value.get('computer') != computer:
        raise UpdateError('Elevhub sendte en ugyldig opdateringsstatus.')
    available = value.get('available')
    if available is not None:
        if not isinstance(available, dict):
            raise UpdateError('Elevhub sendte en ugyldig release.')
        _version_key(available.get('version'))
        if not SHA256.fullmatch(str(available.get('package_sha256', ''))):
            raise UpdateError('Elevhub sendte en ugyldig pakkehash.')
        for key in ('release_id', 'notes'):
            if not isinstance(available.get(key, ''), str) or len(available.get(key, '')) > 4096:
                raise UpdateError('Elevhub sendte ugyldige releaseoplysninger.')
    for key in ('job', 'request'):
        if value.get(key) is not None and not isinstance(value[key], dict):
            raise UpdateError('Elevhub sendte en ugyldig opdateringsstatus.')
    return value


def _installed_version(paths):
    value = _read_json(paths.release)
    version = (value.get('version') or value.get('release_id') or value.get('release')
               if isinstance(value, dict) else None)
    if version in (None, ''):
        return 'Ukendt'
    return version if isinstance(version, str) and VERSION.fullmatch(version) else 'Ukendt'


def _theme(kiosk):
    value = kiosk.get('theme')
    return value if value in ('red', 'yellow', 'green') else 'red'


def read_remote_state(paths=PRODUCTION_PATHS, *, client=None):
    kiosk, computer, _token, _username, _ca = _validate_local_config(paths)
    if client is None:
        client = _client_module(paths.client)
    try:
        raw = _request(paths, client)
    except UpdateError as exc:
        return {
            'installed_version': _installed_version(paths),
            'available': None, 'job': None, 'request': None,
            'feed_state': 'offline', 'theme': _theme(kiosk),
            'install_ready': _local_stage_ready(paths, computer),
            'error': str(exc),
        }
    response = _validate_state(raw, computer)
    available = response.get('available')
    current = _installed_version(paths)
    newer = bool(available and current != 'Ukendt' and
                 _version_key(available['version']) > _version_key(current))
    return {
        'installed_version': current,
        'available': available if newer else None,
        'job': response.get('job'),
        'request': response.get('request'),
        'feed_state': response.get('feed_state', 'unknown'),
        'theme': _theme(kiosk),
        'install_ready': _local_stage_ready(paths, computer),
    }


def _next_request_id(factory=None):
    value = (factory or (lambda: secrets.token_hex(16)))()
    if not isinstance(value, str) or not REQUEST_ID.fullmatch(value):
        raise UpdateError('Enhedens opdateringsanmodning kunne ikke oprettes.')
    return value


def _candidate(response, installed):
    available = response.get('available')
    if available is None or _version_key(available['version']) <= _version_key(installed):
        raise UpdateError('Der er ingen nyere version klar til denne computer.')
    return available


def prepare_update(paths=PRODUCTION_PATHS, *, client=None, request_id_factory=None):
    kiosk, computer, _token, _username, _ca = _validate_local_config(paths)
    if client is None:
        client = _client_module(paths.client)
    state = _validate_state(_request(paths, client), computer)
    installed = _installed_version(paths)
    if installed == 'Ukendt':
        raise UpdateError('Den installerede version kan ikke bekræftes.')
    candidate = _candidate(state, installed)
    remote_request = state.get('request') or {}
    active_statuses = {'requested', 'waiting_for_package', 'queued'}
    job = state.get('job') or {}
    completed_for_prior_release = (
        job.get('status') == 'completed' and
        job.get('release_id') != candidate.get('release_id'))
    if (remote_request.get('status') in active_statuses and
            (remote_request.get('version') != candidate['version'] or
             remote_request.get('package_sha256') != candidate['package_sha256']) and
            not completed_for_prior_release):
        selected = remote_request.get('version') or 'en ukendt version'
        raise UpdateError(
            f'Version {selected} er allerede valgt og afventer behandling. '
            'Vent, eller brug “Prøv igen”, før du vælger en anden version.')
    existing = None
    try:
        existing = _read_private_json(paths.request)
    except UpdateError:
        pass
    old_approval_exists = Path(paths.approval).exists()
    old_request_exists = Path(paths.request).exists()
    old_approval = _read_private_json(paths.approval) if old_approval_exists else None
    old_request = _read_private_json(paths.request) if old_request_exists else None
    reusable = (isinstance(existing, dict) and
            existing.get('schema') == 1 and existing.get('computer') == computer and
            existing.get('version') == candidate['version'] and
            existing.get('package_sha256') == candidate['package_sha256'] and
            REQUEST_ID.fullmatch(str(existing.get('request_id', ''))))
    if (reusable and isinstance(old_approval, dict) and
            old_approval.get('computer') == computer and
            old_approval.get('version') == candidate['version'] and
            old_approval.get('package_sha256') == candidate['package_sha256']):
        request_id = existing.get('request_id')
    else:
        request_id = _next_request_id(request_id_factory)
        approval = {'schema': 1, 'computer': computer,
                    'version': candidate['version'],
                    'package_sha256': candidate['package_sha256'],
                    'restart_authorized': False}
        write_json(paths.approval, approval)
        existing = {'schema': 1, 'computer': computer, 'request_id': request_id,
                    'version': candidate['version'],
                    'package_sha256': candidate['package_sha256']}
        write_json(paths.request, existing)
    if not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id):
        raise UpdateError('Den gemte opdateringsanmodning er ugyldig.')
    payload = {'request_id': request_id, 'action': 'update',
               'version': candidate['version'],
               'package_sha256': candidate['package_sha256']}
    try:
        response = _validate_state(_request(paths, client, data=payload), computer)
        accepted_request = response.get('request')
        if (not isinstance(accepted_request, dict) or
                accepted_request.get('version') != candidate['version'] or
                accepted_request.get('package_sha256') != candidate['package_sha256']):
            raise ServerRejected(
                'Elevhub beholdt et andet opdateringsvalg. Det tidligere valg er bevaret.')
    except ServerRejected:
        _restore_json(paths.approval, old_approval_exists, old_approval)
        _restore_json(paths.request, old_request_exists, old_request)
        raise
    return {'ok': True, 'installed_version': installed,
            'available': candidate, 'job': response.get('job'),
            'request': response.get('request'), 'feed_state': response.get('feed_state', 'unknown'),
            'theme': _theme(kiosk), 'install_ready': _local_stage_ready(paths, computer)}


def _restore_json(path, existed, value):
    if existed:
        write_json(path, value)
    else:
        try:
            Path(path).unlink()
        except FileNotFoundError:
            pass


def retry_update(paths=PRODUCTION_PATHS, *, client=None, request_id_factory=None):
    kiosk, computer, _token, _username, _ca = _validate_local_config(paths)
    if client is None:
        client = _client_module(paths.client)
    saved = _read_private_json(paths.request)
    if (not isinstance(saved, dict) or saved.get('schema') != 1 or
            saved.get('computer') != computer or
            not REQUEST_ID.fullmatch(str(saved.get('request_id', ''))) or
            not SHA256.fullmatch(str(saved.get('package_sha256', ''))) or
            not VERSION.fullmatch(str(saved.get('version', '')))):
        raise UpdateError('Der er ingen gyldig opdateringsanmodning at prøve igen.')
    approval = _read_private_json(paths.approval)
    if (not isinstance(approval, dict) or
            {key: approval.get(key) for key in ('schema', 'computer', 'version', 'package_sha256')} !=
            {'schema': 1, 'computer': computer, 'version': saved['version'],
             'package_sha256': saved['package_sha256']}):
        raise UpdateError('Godkendelsen passer ikke til den gemte opdateringsanmodning.')
    retry_id = _next_request_id(request_id_factory)
    saved['retry_request_id'] = retry_id
    write_json(paths.request, saved)
    payload = {'request_id': retry_id, 'action': 'retry',
               'version': saved['version'],
               'package_sha256': saved['package_sha256']}
    response = _validate_state(_request(paths, client, data=payload), computer)
    return {'ok': True, 'installed_version': _installed_version(paths),
            'available': None, 'job': response.get('job'),
            'request': response.get('request'), 'feed_state': response.get('feed_state', 'unknown'),
            'theme': _theme(kiosk), 'install_ready': _local_stage_ready(paths, computer)}


def check_update(paths=PRODUCTION_PATHS, *, client=None, request_id_factory=None):
    kiosk, computer, _token, _username, _ca = _validate_local_config(paths)
    if client is None:
        client = _client_module(paths.client)
    request_id = _next_request_id(request_id_factory)
    response = _request(paths, client, data={'request_id': request_id, 'action': 'check'})
    _validate_state(response, computer)
    return read_remote_state(paths, client=client)


def _safe_root_file(path, *, max_bytes=MAX_JSON, private=False):
    try:
        info = os.lstat(path)
    except OSError:
        return None
    unsafe_mode = info.st_mode & (0o077 if private else 0o022)
    if (not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes or
            os.name != 'nt' and (info.st_uid != 0 or unsafe_mode)):
        return None
    return info


def _safe_root_directory(path, *, private=False):
    try:
        info = os.lstat(path)
    except OSError:
        return False
    unsafe_mode = info.st_mode & (0o077 if private else 0o022)
    return (stat.S_ISDIR(info.st_mode) and
            (os.name == 'nt' or info.st_uid == 0 and not unsafe_mode))


def _staged_release(paths, approval):
    digest = approval['package_sha256']
    operation = 'github-linux-' + digest[:24]
    stage = Path(paths.update_root) / ('github-linux-' + digest[:24])
    journal_path, pins_path, installer_path = stage / 'journal.json', stage / 'pins.json', stage / 'install.py'
    if not _safe_root_directory(paths.update_root) or not _safe_root_directory(stage, private=True):
        return None
    for path in (journal_path, pins_path):
        limit = MAX_PINS_BYTES if path == pins_path else MAX_JSON
        if _safe_root_file(path, max_bytes=limit, private=True) is None:
            return None
    if _safe_root_file(installer_path, max_bytes=MAX_INSTALLER_BYTES) is None:
        return None
    try:
        journal = _read_json(journal_path)
        pins = _read_json(pins_path, max_bytes=MAX_PINS_BYTES)
        installer = installer_path.read_text(encoding='utf-8')
    except (OSError, UnicodeError, UpdateError):
        return None
    if (not isinstance(journal, dict) or not isinstance(pins, dict) or
            journal.get('status') != 'staged_awaiting_restart' or
            journal.get('computer') != approval['computer'] or
            journal.get('release') != approval['version'] or
            journal.get('operation') != operation or
            journal.get('package_sha256') != digest or
            pins.get('release') != approval['version'] or
            pins.get('package_sha256') != digest or
            not re.fullmatch(r'[a-f0-9]{64}', str(pins.get('payload_sha256', ''))) or
            not isinstance(pins.get('manifest'), dict) or
            pins['manifest'].get('version') != approval['version']):
        return None
    match = re.search(r"^PACKAGE_SHA\s*=\s*['\"]([a-f0-9]{64})['\"]\s*$", installer, re.M)
    if not match or match.group(1) != digest:
        return None
    return journal


def _local_stage_ready(paths, computer):
    try:
        approval = _read_private_json(paths.approval)
        if (not isinstance(approval, dict) or set(approval) != {
                'schema', 'computer', 'version', 'package_sha256', 'restart_authorized'} or
                approval.get('schema') != 1 or approval.get('computer') != computer or
                not VERSION.fullmatch(str(approval.get('version', ''))) or
                not SHA256.fullmatch(str(approval.get('package_sha256', ''))) or
                type(approval.get('restart_authorized')) is not bool):
            return False
        return _staged_release(paths, approval) is not None
    except UpdateError:
        return False


def authorize_restart(approval, *, computer, staged, preservation_needed=False,
                      write_approval, reboot):
    """Persist explicit approval and reboot only after strict package binding."""
    if not isinstance(approval, dict) or set(approval) != {
            'schema', 'computer', 'version', 'package_sha256', 'restart_authorized'}:
        raise UpdateError('Installationsgodkendelsen er ugyldig.')
    if (approval['schema'] != 1 or approval['computer'] != computer or
            not VERSION.fullmatch(str(approval['version'])) or
            not SHA256.fullmatch(str(approval['package_sha256'])) or
            type(approval['restart_authorized']) is not bool):
        raise UpdateError('Installationsgodkendelsen passer ikke til denne computer.')
    expected_operation = 'github-linux-' + approval['package_sha256'][:24]
    if (not isinstance(staged, dict) or
            staged.get('status') != 'staged_awaiting_restart' or
            staged.get('computer') != computer or
            staged.get('receipt_computer', computer) != computer or
            staged.get('version', staged.get('release')) != approval['version'] or
            staged.get('release', staged.get('version')) != approval['version'] or
            staged.get('package_sha256') != approval['package_sha256'] or
            staged.get('pins_package_sha256', approval['package_sha256']) != approval['package_sha256'] or
            staged.get('operation') != expected_operation or
            staged.get('installer_package_sha256', approval['package_sha256']) != approval['package_sha256'] or
            preservation_needed):
        raise UpdateError('Ingen matchet, klar opdateringspakke er fundet. Computeren genstartes ikke.')
    if not approval['restart_authorized']:
        updated = dict(approval)
        updated['restart_authorized'] = True
        write_approval(updated)
    reboot()
    return True


def _restart(paths=PRODUCTION_PATHS, *, run=subprocess.run):
    _kiosk, computer, _token, _username, _ca = _validate_local_config(paths)
    approval = _read_private_json(paths.approval)
    if (not isinstance(approval, dict) or set(approval) != {
            'schema', 'computer', 'version', 'package_sha256', 'restart_authorized'} or
            approval.get('schema') != 1 or approval.get('computer') != computer or
            not VERSION.fullmatch(str(approval.get('version', ''))) or
            not SHA256.fullmatch(str(approval.get('package_sha256', ''))) or
            type(approval.get('restart_authorized')) is not bool):
        raise UpdateError('Installationsgodkendelsen er ugyldig. Computeren genstartes ikke.')
    staged = _staged_release(paths, approval)
    if staged is None:
        raise UpdateError('Ingen matchet, klar GitHub-opdatering er fundet. Computeren genstartes ikke.')
    digest = approval['package_sha256']
    stage = Path(paths.update_root) / ('github-linux-' + digest[:24])
    pins = _read_json(stage / 'pins.json', max_bytes=MAX_PINS_BYTES)
    bound = dict(staged)
    bound.update({'version': staged.get('release'),
                  'pins_package_sha256': pins.get('package_sha256'),
                  'installer_package_sha256': digest})
    authorize_restart(approval, computer=computer, staged=bound,
                      write_approval=lambda value: write_json(paths.approval, value),
                      reboot=lambda: run(['/usr/bin/systemctl', 'reboot'], check=True))
    return {'ok': True, 'status': 'restarting', 'version': approval['version']}


def perform(action, paths=PRODUCTION_PATHS, *, client=None, run=subprocess.run):
    if action == 'state':
        return read_remote_state(paths, client=client)
    if action == 'check':
        return check_update(paths, client=client)
    if action == 'update':
        return prepare_update(paths, client=client)
    if action == 'retry':
        return retry_update(paths, client=client)
    if action == 'restart':
        return _restart(paths, run=run)
    raise UpdateError('Ukendt handling.')


def _authorize_sudo(paths=PRODUCTION_PATHS, environ=os.environ):
    if os.geteuid() != 0:
        raise UpdateError('Opdateringshjælperen skal startes gennem sudo.')
    _kiosk, _computer, _token, username, _ca = _validate_local_config(paths)
    if environ.get('SUDO_USER') != username:
        raise UpdateError('Kun den konfigurerede lokale administrator må bruge opdateringshjælperen.')


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        action = parse_action(argv)
        _authorize_sudo()
        result = perform(action)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (UpdateError, ValueError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
