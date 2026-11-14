#!/usr/bin/env python3
"""Validate one source app with the canonical catalog and package ownership gate."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).absolute().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'system')]
from app.app_catalog import component_for_app
from app.app_packages import prepare, validate_spec
from sdk.new_app import plain


def check_app(root, app_id, stage):
    component_for_app(app_id)
    root = plain(root)
    folder = plain(root / 'apps' / app_id)
    descriptor = json.loads(plain(folder / 'app.json').read_text(encoding='utf-8'))
    if descriptor['id'] != app_id:
        raise ValueError('sdk_app_identity_mismatch')
    manifest = json.loads(plain(folder / 'files.json').read_text(encoding='utf-8'))
    # A source template must stay in its own app, even if another app's file is public.
    prefix = f'apps/{app_id}/'
    for item in manifest.get('files', []):
        if not item.get('source', '').startswith(prefix):
            raise ValueError('sdk_source_outside_app')
    spec = prepare(root, descriptor, manifest, stage)
    validate_spec(spec)
    return spec


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', required=True)
    parser.add_argument('--source-root', type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        with tempfile.TemporaryDirectory(prefix='mkv-sdk-check-') as temporary:
            spec = check_app(args.source_root, args.app, Path(temporary))
            print(json.dumps({'checked': args.app, 'package': spec['package'], 'files': len(spec['files'])}))
    except (ValueError, OSError) as error:
        parser.exit(2, str(error) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
