#!/usr/bin/env python3
"""Prepare one independent app input lock from its reviewed public manifest."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.app_packages import prepare


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-root', type=Path, required=True)
    p.add_argument('--app', required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--scratch-lock', type=Path)
    p.add_argument('--vendor-built-from',type=Path)
    a = p.parse_args()
    from app.app_catalog import component_for_app
    component_for_app(a.app)
    folder = a.source_root/'apps'/a.app
    spec = prepare(a.source_root, json.loads((folder/'app.json').read_text(encoding='utf-8')),
                   json.loads((folder/'files.json').read_text(encoding='utf-8')), a.output_dir,
                   scratch_lock=json.loads(a.scratch_lock.read_text()) if a.scratch_lock else None,
                   vendor_built_from=a.vendor_built_from)
    path = a.output_dir/(spec['package']+'.json')
    path.write_text(json.dumps(spec, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    print(json.dumps({'package': spec['package'], 'version': spec['version'], 'files': len(spec['files'])}))

if __name__ == '__main__': main()
