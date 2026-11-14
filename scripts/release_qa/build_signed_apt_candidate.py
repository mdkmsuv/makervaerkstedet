"""Build a private candidate apt repository with the existing local publisher key.

Copies the packages named in the install-simulation receipt from the build work
folder, writes the repository inputs, and runs scripts/build_apt_repository.py
under WSL with a temporary signing home from scripts/local_release_signing.py.

The result is a private candidate. It is not published and not installed.
Everything is written to the new --output folder:
  linux-packages/, apt-inputs.json, apt/, apt-receipt.json, apt-build.log
"""
import argparse
import hashlib
import importlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'system')]
from scripts import build_apt_repository as publisher

DEFAULT_GPG = 'C:/Program Files/Git/usr/bin/gpg.exe'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def linux(path):
    """Windows path on a drive letter -> the same path as seen from WSL."""
    path = Path(path).absolute()
    if len(path.drive) != 2 or path.drive[1] != ':':
        raise SystemExit('Stien kan ikke bruges fra WSL (kræver et drevbogstav): ' + str(path))
    return '/mnt/' + path.drive[0].lower() + path.as_posix()[2:]


def read_json(path, limit=16 * 1024**2):
    path = publisher.plain(path)
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError('candidate_regular_input_required')
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate_candidate_input_field')
            value[key] = item
        return value
    return json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=unique)


def modern_descriptors(upstream, gate):
    """Bind descriptors to the exact reviewed locks and native build receipts."""
    folder = publisher.plain(upstream / 'locks')
    if not folder.is_dir():
        raise ValueError('candidate_input_locks_required')
    locks = {}
    for path in folder.glob('*.json'):
        lock = read_json(path)
        if type(lock) is not dict:
            raise ValueError('invalid_candidate_input_lock')
        if not isinstance(lock.get('schema'), str) or not lock['schema'].startswith('makervaerkstedet.debian-inputs.'):
            if path.name.startswith('makervaerkstedet-'):
                raise ValueError('invalid_candidate_input_lock')
            continue  # Receipt-bound descriptor/runtime staging files are not package locks.
        app = lock['schema'] == 'makervaerkstedet.debian-inputs.v2'
        fields = {'schema', 'package', 'version', 'architecture', 'depends', 'files', 'provenance'}
        if set(lock) != fields | ({'app_descriptor'} if app else set()) or lock['schema'] not in (
                'makervaerkstedet.debian-inputs.v1', 'makervaerkstedet.debian-inputs.v2'):
            raise ValueError('invalid_candidate_input_lock')
        name = lock['package']
        if not isinstance(name, str) or path.name != name + '.json' or name in locks:
            raise ValueError('ambiguous_candidate_input_lock')
        locks[name] = lock
    if set(locks) != set(gate['packages']):
        raise ValueError('exact_candidate_input_locks_required')
    descriptors = []
    for name, lock in sorted(locks.items()):
        entry = gate['packages'][name]
        if any(lock[key] != entry[key] for key in ('version', 'architecture')):
            raise ValueError('candidate_lock_identity_mismatch')
        receipt = read_json((upstream / 'debian' / entry['file']).with_suffix('.inputs.json'))
        fields = {'package', 'version', 'architecture', 'bytes', 'sha256', 'input_lock_sha256', 'provenance', 'files'}
        if type(receipt) is not dict or set(receipt) != fields:
            raise ValueError('invalid_candidate_package_receipt')
        if (receipt['package'] != name or any(receipt[key] != entry[key] for key in ('version', 'architecture', 'sha256'))
                or type(receipt['bytes']) is not int or receipt['bytes'] != (upstream / 'debian' / entry['file']).stat().st_size):
            raise ValueError('candidate_package_receipt_mismatch')
        if type(lock['files']) is not list or not lock['files'] or any(type(item) is not dict for item in lock['files']):
            raise ValueError('invalid_candidate_lock_inventory')
        inventory = [{key: value for key, value in item.items() if key != 'source'} for item in lock['files']]
        if inventory != receipt['files']:
            raise ValueError('candidate_lock_inventory_mismatch')
        if not isinstance(receipt['input_lock_sha256'], str) or not re.fullmatch('[a-f0-9]{64}', receipt['input_lock_sha256']):
            raise ValueError('invalid_candidate_lock_hash')
        if name != publisher.DEBIAN_COMPONENTS['matchbox'][0]:
            canonical = hashlib.sha256(json.dumps(lock, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            if canonical != receipt['input_lock_sha256']:
                raise ValueError('candidate_reviewed_input_lock_mismatch')
        if 'app_descriptor' in lock:
            descriptors.append(lock['app_descriptor'])
        elif name not in publisher.BASE_PACKAGES:
            raise ValueError('candidate_app_descriptor_required')
        # The pinned reused Matchbox archive retains its historical receipt;
        # its exact identity/inventory are checked without absolute source paths.
    return descriptors


def prepare_inputs(upstream, output, *, path_mapper=linux):
    """Read/validate only; return the producer spec and bounded copy plan.

    No directory, package, key, signing home or repository is changed here.
    The producer rechecks native control fields and embedded app descriptors.
    """
    upstream, output = publisher.plain(upstream), publisher.plain(output)
    if output.exists() or upstream == output or upstream in output.parents or output in upstream.parents:
        raise ValueError('candidate_output_must_be_new_and_separate')
    source_receipt = upstream / 'report/install-simulation.json'
    gate = read_json(source_receipt, limit=1024**2)
    if type(gate) is not dict or type(gate.get('schema')) is not int or gate['schema'] not in (1, 2) or type(gate.get('packages')) is not dict:
        raise ValueError('invalid_candidate_install_gate')
    modern = gate['schema'] == 2
    release_set = gate.get('release_set') if modern else 'legacy'
    if modern and release_set not in ('base', 'app', 'migration'):
        raise ValueError('invalid_candidate_release_set')
    packages = output / 'linux-packages'
    target_receipt = packages / 'install-simulation.json'
    copies = [(source_receipt, target_receipt)]
    records = []
    filenames = set()
    for name, entry in sorted(gate['packages'].items()):
        if type(entry) is not dict or set(entry) != {'file', 'version', 'architecture', 'sha256'}:
            raise ValueError('invalid_candidate_package')
        filename = entry['file']
        if (not isinstance(filename, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._+~%-]*\.deb', filename)
                or filename != f"{name}_{entry['version']}_{entry['architecture']}.deb" or filename.casefold() in filenames):
            raise ValueError('unsafe_candidate_package_filename')
        filenames.add(filename.casefold())
        source = publisher.plain(upstream / 'debian' / filename)
        if not source.is_file() or sha(source) != entry['sha256']:
            raise ValueError('candidate_package_hash_mismatch')
        records.append(dict(path=str(source), package=name, version=entry['version'],
                            architecture=entry['architecture'], size=source.stat().st_size, sha256=entry['sha256']))
        copies.append((source, packages / filename))
    observed = [path.name.casefold() for path in (upstream / 'debian').iterdir() if path.suffix.lower() == '.deb']
    if set(observed) != filenames or len(observed) != len(filenames):
        raise ValueError('exact_candidate_package_files_required')
    descriptors = modern_descriptors(upstream, gate) if modern else []
    native_gate = dict(path=str(source_receipt), sha256=sha(source_receipt))
    publisher.validate_install_gate(native_gate, records, release_set=release_set, app_descriptors=descriptors)
    spec = dict(schema=gate['schema'], packages=[dict(record, path=path_mapper(packages / Path(record['path']).name)) for record in records],
                test_repository=False, install_gate=dict(path=path_mapper(target_receipt), sha256=native_gate['sha256']))
    if modern:
        spec.update(release_set=release_set, app_descriptors=descriptors)
    return spec, copies


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source', required=True, type=Path,
                        help='checkout of the source whose scripts/ folder is used')
    parser.add_argument('--upstream', required=True, type=Path,
                        help='build work folder containing report/install-simulation.json and debian/')
    parser.add_argument('--output', required=True, type=Path, help='new output folder; refused if it exists')
    parser.add_argument('--gpg', type=Path, default=Path(DEFAULT_GPG), help='native gpg.exe (default: %(default)s)')
    parser.add_argument('--distro', default='Debian', help='WSL distribution that runs the repository builder')
    args = parser.parse_args(argv)

    scripts = args.source / 'scripts'
    if not (scripts / 'local_release_signing.py').is_file() or not (scripts / 'build_apt_repository.py').is_file():
        raise SystemExit('--source mangler scripts/local_release_signing.py eller scripts/build_apt_repository.py')
    if not (args.upstream / 'report' / 'install-simulation.json').is_file():
        raise SystemExit('--upstream mangler report/install-simulation.json')
    if args.output.exists():
        raise SystemExit('Outputmappen findes allerede og overskrives ikke: ' + str(args.output))

    base = args.output.absolute()
    inputs, copies = prepare_inputs(args.upstream, base)
    expected = {Path(entry['path']).name: entry['sha256'] for entry in inputs['packages']}
    expected['install-simulation.json'] = inputs['install_gate']['sha256']
    for source, target in copies:
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open('rb') as incoming, target.open('xb') as outgoing:
            shutil.copyfileobj(incoming, outgoing)
        if sha(target) != expected[target.name]:
            raise ValueError('candidate_input_changed_during_copy')
    spec = base / 'apt-inputs.json'
    spec.write_text(json.dumps(inputs, indent=2, ensure_ascii=False), encoding='utf-8')
    sys.path.insert(0, str(scripts.resolve()))
    apt_signing_home = importlib.import_module('local_release_signing').apt_signing_home
    with apt_signing_home(gpg_exe=args.gpg) as (home, fingerprint, tool_sha):
        command = ['wsl', '-d', args.distro, '-u', 'root', '--', 'python3', '-B',
                   linux(scripts / 'build_apt_repository.py'), '--inputs', linux(spec),
                   '--output', linux(base / 'apt'), '--gpg-exe', linux(args.gpg),
                   '--gpg-sha256', tool_sha, '--gnupg-home', linux(home),
                   '--key-fingerprint', fingerprint, '--receipt', linux(base / 'apt-receipt.json')]
        with (base / 'apt-build.log').open('w', encoding='utf-8') as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    print('Signed local apt repository built; not published or installed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
