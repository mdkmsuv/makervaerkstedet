r"""Sign release candidate files with minisign and write a receipt.

Uses scripts/local_release_signing.py from this source tree. Each file gets a
<file>.minisig next to it; existing signatures are never overwritten. The
receipt is a new JSON file; it is refused if it already exists. File names in
the receipt are relative to the receipt's folder where possible.

Example:
    python sign_candidates.py --minisign minisign.exe --receipt minisign-receipt.json \
        --source-commit <40 hex> candidate.iso setup.exe
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]


def relative(path, folder):
    try:
        return Path(os.path.relpath(path, folder)).as_posix()
    except ValueError:  # other drive
        return Path(path).name


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--minisign', required=True, type=Path, help='path to minisign.exe')
    parser.add_argument('--receipt', required=True, type=Path, help='new receipt file (JSON)')
    parser.add_argument('--source-commit', required=True, help='full 40-character commit the candidates were built from')
    parser.add_argument('files', nargs='+', type=Path, help='files to sign')
    args = parser.parse_args(argv)
    if not re.fullmatch(r'[0-9a-f]{40}', args.source_commit):
        parser.error('--source-commit must be 40 lowercase hex characters')
    if args.receipt.exists():
        parser.error('receipt already exists and is not overwritten: ' + str(args.receipt))
    for path in args.files:
        if not path.is_file():
            parser.error('file not found: ' + str(path))

    sys.path.insert(0, str(SCRIPTS))
    import local_release_signing as signing

    folder = args.receipt.resolve().parent
    out = []
    for path in args.files:
        record = signing.sign_minisign(str(path.resolve()), minisign_exe=str(args.minisign))
        record['file'] = relative(path.resolve(), folder)
        record['signature'] = record['file'] + '.minisig'
        out.append(record)
    args.receipt.write_text(json.dumps({'schema': 1, 'source_commit': args.source_commit, 'signed': out}, indent=2), encoding='utf-8')
    print(json.dumps(out, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
