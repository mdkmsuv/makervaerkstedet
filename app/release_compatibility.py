"""Offline preflight for a complete Makerspace program release and its data.

Only reads program and business data. The shared maintenance context holds
service locks; no processes, installers, Python payloads or hardware are run.
Checksums prove consistency with this manifest, not publisher authenticity.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

from .data_contract import require_contract, inspect_component
from .installation_backup import _plain, _relative, _json, _hash, _scan, offline_profile, DATABASES

MANIFEST = 'makerspace-release.json'
CREDIT = 'Kreditering: Klostermarksskolen.'
ROLES = {'hub', 'print', 'laser'}
SHARED = {'CREDITS.md', 'app/__init__.py', 'app/instance.py', 'app/backup_guard.py',
          'app/backup_databases.py', 'app/installation_backup.py', 'app/data_contract.py',
          'app/release_compatibility.py', 'app/database_migrations.py',
          'app/installation_update.py', 'app/generation_runtime.py',
          'app/generation_relocation.py', 'app/generation_transaction.py',
          'app/generation_materialization.py', 'app/generation_activation.py', 'app/generation_host.py',
          'app/generation_rollback.py',
          'app/generation_selection.py', 'app/generation_lock.py', 'server/bootstrap.py'}
REQUIRED = {
    'hub': {'deployment/school-setup/enrollment.py', 'app/school_admin.py', 'app/school_backup.py', 'hub/elevhub.py', 'hub/data_lock.py', 'hub/makerspace_api.py',
            'hub/makerspace_store.py', 'hub/makerspace_workflows.py',
            'hub/makerspace_printers.py', 'hub/makerspace_laser_admin.py',
            'hub/makerspace_laser.py', 'hub/system_overview.py',
            'hub/web/makerspace.html', 'hub/web/makerspace.js', 'hub/web/makerspace.css',
            'hub/web/makerspace-printers.js',
            'hub/web/makerspace-print-workflows.js', 'hub/web/makerspace-lasers.js'},
    'print': {'app/server.py', 'app/core.py', 'app/printer_bridge.py',
              'app/hardware_workflows.py', 'app/background.py'},
    'laser': {'app/laser_server.py', 'app/laser_workflows.py', 'app/laser_station.py',
              'app/hardware_workflows.py', 'app/laser_inbox_lifecycle.py'},
}
# External runtimes and dynamic imports need separate package validation.
# This pass follows local Python source imports without importing the payload.
EXTERNAL = {'PIL', 'paho', 'cryptography', 'cffi', 'psutil', 'numpy', 'requests'}
MAX_FILES = 200_000
# The optional local image model already contains a 2.55 GiB blob. Hashing is
# streamed, so allow these bundled assets without loading them into memory.
MAX_BYTES = 8 * 1024 * 1024 * 1024


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Dubleret nøgle i udgivelsesmanifest.')
        result[key] = value
    return result


def _manifest(root):
    path = _plain(root / MANIFEST, False)
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError('Udgivelsesmanifestet er for stort.')
    raw = path.read_bytes()
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique)
    if (not isinstance(value, dict) or set(value) != {'format', 'product', 'release', 'credits', 'data_contract', 'files'}
            or type(value['format']) is not int or value['format'] != 1
            or value['product'] != 'Makerværkstedet' or value['credits'] != CREDIT
            or not isinstance(value['release'], str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}', value['release'])
            or not isinstance(value['files'], dict) or not 1 <= len(value['files']) <= MAX_FILES):
        raise ValueError('Ukendt eller ufuldstændigt udgivelsesmanifest.')
    require_contract(value['data_contract'], value['data_contract'])
    return value, hashlib.sha256(raw).hexdigest()


def _module_file(files, module):
    name = module.replace('.', '/')
    for candidate in (name + '.py', name + '/__init__.py'):
        if candidate in files:
            return candidate
    raise ValueError('Udgivelsen mangler en lokal Python-afhængighed: ' + module)


def _dependencies(root, files):
    """Check static source dependencies; do not execute candidate code."""
    for name in sorted(files):
        if not name.endswith('.py') or not name.startswith(('app/', 'hub/', 'server/')):
            continue
        content = _plain(root / name, False).read_bytes()
        if len(content) != files[name]['size'] or hashlib.sha256(content).hexdigest() != files[name]['sha256']:
            raise ValueError('Programfilen ændredes under kontrollen: ' + name)
        if len(content) > 4 * 1024 * 1024:
            raise ValueError('En Python-kildefil er for stor.')
        tree = ast.parse(content, filename=name)
        parts = name[:-3].split('/')
        package = parts[:-1]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    if node.level > len(package):
                        raise ValueError('Ugyldig relativ import: ' + name)
                    prefix = package[:len(package) - node.level + 1]
                    module = '.'.join(prefix + ([node.module] if node.module else []))
                    _module_file(files, module)
                    if node.module is None:
                        for alias in node.names:
                            if alias.name != '*':
                                _module_file(files, module + '.' + alias.name)
                    continue
                modules = [node.module] if node.module else []
            else:
                continue
            for module in modules:
                first = module.split('.')[0]
                if first in ('app', 'server'):
                    _module_file(files, module)
                elif name.startswith('hub/') and first not in sys.stdlib_module_names | EXTERNAL | {'__future__'}:
                    _module_file(files, 'hub.' + module)


def inspect_program(program_root):
    root = _plain(Path(program_root), True)
    manifest, manifest_hash = _manifest(root)
    required = SHARED | set().union(*(REQUIRED[role] for role in manifest['data_contract']))
    if not required <= manifest['files'].keys():
        raise ValueError('Udgivelsen mangler dele af den samlede makerspace-modulgruppe.')
    inventory = _scan(root, ignore_locks=False)
    if set(inventory[0]) != set(manifest['files']) | {MANIFEST}:
        raise ValueError('Programmappens filer matcher ikke udgivelsesmanifestet.')
    seen = set()
    for name, record in manifest['files'].items():
        _relative(name)
        if name == MANIFEST or name.casefold() in seen:
            raise ValueError('Dubleret eller reserveret programsti.')
        seen.add(name.casefold())
        if (not isinstance(record, dict) or set(record) != {'sha256', 'size'}
                or not isinstance(record['sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', record['sha256'])
                or type(record['size']) is not int or not 0 <= record['size'] <= MAX_BYTES):
            raise ValueError('Ugyldig filspecifikation i udgivelsen.')
        path = _plain(root / name, False)
        if path.stat().st_size != record['size'] or _hash(path) != record['sha256']:
            raise ValueError('Programfilens kontrolsum passer ikke: ' + name)
    if CREDIT not in (root / 'CREDITS.md').read_text(encoding='utf-8'):
        raise ValueError('Klostermarksskolens kreditering mangler.')
    _dependencies(root, manifest['files'])
    if _hash(root / MANIFEST) != manifest_hash or _scan(root, ignore_locks=False) != inventory:
        raise ValueError('Udgivelsesmanifestet ændredes under kontrollen.')
    return {'release': manifest['release'], 'manifest_sha256': manifest_hash,
            'data_contract': manifest['data_contract'], 'files': len(manifest['files'])}


def check(profile, program_root):
    # Locks must still be acquired by a future apply/rollback transaction; a
    # saved result from this preflight is never authority to alter files later.
    with offline_profile(profile) as (_profile, roots, components, _config):
        program = _plain(Path(program_root), True)
        if any(program == root or program.is_relative_to(root) or root.is_relative_to(program) for root in roots.values()):
            raise ValueError('Programfiler og driftsdata skal ligge i adskilte mapper.')
        release = inspect_program(program)
        actual = {role: inspect_component(path / DATABASES[role], role) for role, path in components.items()}
        require_contract(release['data_contract'], actual)
        return {'compatible_at_check': True, 'deployment_ready': False,
                'release': release, 'data': actual, 'credits': CREDIT,
                'remaining': ['program_transaction', 'backup_restore_validation', 'physical_queue_reconciliation']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--program-root', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = check(_json(_plain(args.profile, False)), args.program_root)
    except (OSError, ValueError, RuntimeError, SyntaxError, sqlite3.Error) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps({'ok': True, 'result': result}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
