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
import shutil
import subprocess
import sys
from pathlib import Path

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

    sys.path.insert(0, str(scripts.resolve()))
    apt_signing_home = importlib.import_module('local_release_signing').apt_signing_home

    base = args.output.absolute()
    packages = base / 'linux-packages'
    packages.mkdir(parents=True, exist_ok=False)
    receipt = packages / 'install-simulation.json'
    shutil.copyfile(args.upstream / 'report/install-simulation.json', receipt)
    gate = json.loads(receipt.read_bytes())
    if not (gate['all_packages_simulated'] is True and gate['actual_install'] is False):
        raise SystemExit('Installationssimuleringen er ikke bestået eller er en rigtig installation.')
    records = []
    for name, record in sorted(gate['packages'].items()):
        target = packages / record['file']
        shutil.copyfile(args.upstream / 'debian' / record['file'], target)
        if sha(target) != record['sha256']:
            raise SystemExit('Pakkens SHA-256 passer ikke til kvitteringen: ' + record['file'])
        records.append(dict(path=linux(target), package=name, version=record['version'],
                            architecture=record['architecture'], size=target.stat().st_size, sha256=record['sha256']))
    spec = base / 'apt-inputs.json'
    spec.write_text(json.dumps(dict(schema=1, packages=records, test_repository=False,
                                    install_gate=dict(path=linux(receipt), sha256=sha(receipt))), indent=2), encoding='utf-8')
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
