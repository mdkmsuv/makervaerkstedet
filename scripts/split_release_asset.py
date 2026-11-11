"""Split a verified release into GitHub-sized assets; never upload anything."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

PART_BYTES = 1_900_000_000  # below GitHub's 2 GiB limit
JOINER = Path(__file__).resolve().parents[1] / 'deployment/school-setup/Join-Release.ps1'


def split(source: Path, output: Path, part_bytes: int = PART_BYTES) -> dict:
    source = source.resolve(strict=True)
    if not source.is_file() or source.stat().st_size == 0:
        raise ValueError('Kilden skal være en ikke-tom fil.')
    if not 1 <= part_bytes < 2**31:
        raise ValueError('Hver del skal være mindre end 2 GiB.')
    # A new directory avoids mixing old and new release parts.
    output.mkdir(parents=True, exist_ok=False)
    whole = hashlib.sha256()
    parts = []
    with source.open('rb') as stream:
        while True:
            block = stream.read(min(part_bytes, 1024 * 1024))
            if not block:
                break
            name = f'{source.name}.part{len(parts) + 1:03}'
            digest = hashlib.sha256()
            size = 0
            with (output / name).open('xb') as target:
                while block:
                    target.write(block)
                    digest.update(block); whole.update(block); size += len(block)
                    if size == part_bytes:
                        break
                    block = stream.read(min(part_bytes - size, 1024 * 1024))
            parts.append({'name': name, 'bytes': size, 'sha256': digest.hexdigest()})
    manifest = {'format': 'elevhub-release-parts-v1', 'file': source.name,
                'bytes': sum(p['bytes'] for p in parts), 'sha256': whole.hexdigest(), 'parts': parts}
    (output / f'{source.name}.parts.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    shutil.copy2(JOINER, output / JOINER.name)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    result = split(args.source, args.output)
    print(json.dumps({'file': result['file'], 'parts': len(result['parts']), 'bytes': result['bytes'], 'sha256': result['sha256']}))
