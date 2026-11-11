"""Write the pinned input file that windows/inno/build.py requires.

The installer builder only accepts an explicit inventory: every package file
and every compiler file with its SHA-256, plus the minisign verifier that the
installed server uses to check later updates. This script produces that
inventory from a package folder built by scripts/build_school_package.py and
from the tool folder (Inno Setup and minisign).

Example:
    python -B windows/inno/prepare_inputs.py --role server --version 2.0.0-20261111.1 \
        --package-root <built package> --tools-root ../byggeinput/vaerktoejer \
        --public-review --output <inputs.json>
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def inventory(root):
    root = Path(root)
    files = {path.relative_to(root).as_posix(): sha(path)
             for path in sorted(root.rglob('*')) if path.is_file()}
    if not files:
        raise ValueError(f'Mappen er tom eller findes ikke: {root}')
    return files


def windows_version(version):
    """2.0.0-20261111.1 -> 2.0.1111.1 (each part must fit in 16 bits)."""
    match = re.fullmatch(r'(\d+)\.(\d+)\.\d+-\d{4}(\d{4})\.(\d+)', version)
    if match is None:
        raise ValueError('Versionen skal have formen 2.0.0-ÅÅÅÅMMDD.N, fx 2.0.0-20261111.1')
    return '.'.join(str(int(part)) for part in match.groups())


def public_key(path):
    lines = [line.strip() for line in Path(path).read_text(encoding='ascii').splitlines() if line.strip()]
    keys = [line for line in lines if not line.startswith('untrusted comment:')]
    if len(keys) != 1:
        raise ValueError(f'Forventede præcis én offentlig nøgle i {path}')
    return keys[0]


def prepare(role, version, package_root, tools_root, public_review):
    tools = Path(tools_root).resolve()
    verifier = tools / 'minisign-windows/minisign-win64/x86_64/minisign.exe'
    licence = tools / 'minisign-0.12-LICENSE'
    provenance = tools / 'minisign-windows-provenance.json'
    compiler = tools / 'inno-7.1.1'
    return {
        'schema': 1, 'role': role, 'version': version, 'windows_version': windows_version(version),
        'compiler_root': str(compiler), 'compiler_files': inventory(compiler),
        'package_root': str(Path(package_root).resolve()), 'files': inventory(package_root),
        'public_review': public_review,
        'release_trust': {
            'minisign_exe': str(verifier), 'minisign_sha256': sha(verifier),
            'public_key': public_key(ROOT / 'deployment/keys/minisign.pub'),
            'license_file': str(licence), 'license_sha256': sha(licence),
            'provenance_file': str(provenance), 'provenance_sha256': sha(provenance),
        },
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--role', choices=('server', 'laser_station'), required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--package-root', type=Path, required=True)
    parser.add_argument('--tools-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--public-review', action='store_true',
                        help='Bekræft, at pakken er gennemgået og ikke indeholder skoledata eller nøgler.')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Outputfilen findes allerede.')
    value = prepare(args.role, args.version, args.package_root, args.tools_root, args.public_review)
    args.output.write_text(json.dumps(value, indent=1), encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'package_files': len(value['files']),
                      'compiler_files': len(value['compiler_files']), 'windows_version': value['windows_version']}))
