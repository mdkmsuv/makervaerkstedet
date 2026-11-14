#!/usr/bin/env python3
"""Create one app-owned, offline SDK example without changing the system."""
from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
import stat
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).absolute().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'system')]
from repository import repo_root
from app.app_catalog import SDK_VERSION, SDK_MIN_CORE, validate_app_descriptor

TEMPLATES = Path(__file__).with_name('templates')


def plain(path):
    """Reject symlinks and Windows junction/reparse ancestors before resolving."""
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('linked_sdk_path')
    return path


def create_app(app_id, name, port, *, project=False, root=None):
    """Python entry point also accepts an isolated repository fixture for tests."""
    root = plain(root if root is not None else repo_root(__file__))
    descriptor = {
        'schema': 2, 'id': app_id, 'navn': name,
        'description': 'Skriv og gem dine noter.' if project else 'En lille quiz, du kan prøve igen.',
        'category': 'ide', 'icon': None, 'version': '1.0.0',
        'package': 'makervaerkstedet-app-' + app_id, 'architecture': 'all',
        'sdk': SDK_VERSION, 'min_core': SDK_MIN_CORE,
        'start': f'http://127.0.0.1:{port}/', 'offline': True,
        'aflevering': None, 'kraever_internet': False, 'licens': 'GPL-3.0-or-later',
        'project': {'kind': app_id, 'extension': '.' + app_id.replace('-', '')[:26] + '.mkv', 'max_bytes': 1048576} if project else None,
        'capabilities': {'required': ['projects-v1'] if project else [], 'optional': ['theme-v1']},
    }
    validate_app_descriptor(descriptor)  # One canonical ID/package/port/project contract.
    apps = plain(root / 'apps')
    if not apps.is_dir():
        raise ValueError('sdk_apps_directory_required')
    for existing in apps.iterdir():
        plain(existing)
        if existing.name.casefold() == app_id.casefold():
            raise ValueError('sdk_app_already_exists')
        manifest = existing / 'app.json'
        if manifest.is_file():
            other = validate_app_descriptor(json.loads(plain(manifest).read_text(encoding='utf-8')))
            if urlsplit(other['start']).port == port:
                raise ValueError('sdk_port_already_used')
            if project and other.get('project') and other['project']['extension'] == descriptor['project']['extension']:
                raise ValueError('sdk_project_extension_already_used')
    mode = 'notes' if project else 'quiz'
    replacements = {'__APP_NAME_HTML__': html.escape(name, quote=True),
                    '__APP_ID_JSON__': json.dumps(app_id),
                    '__APP_EXTENSION_JSON__': json.dumps(descriptor['project']['extension'] if project else None)}
    files = {'app.json': json.dumps(descriptor, ensure_ascii=False, indent=2) + '\n',
             'files.json': json.dumps({'schema': 1, 'files': [{
                 'source': f'apps/{app_id}/web', 'path': f'usr/share/makervaerkstedet/apps/{app_id}/www', 'tree': True}]}, indent=2) + '\n'}
    for source, target in [('index.html', 'index.html'), ('style.css', 'style.css'),
                           (mode + '.html', '_content'), (mode + '.js', 'app.js')]:
        text = plain(TEMPLATES / source).read_text(encoding='utf-8')
        for token, value in replacements.items():
            text = text.replace(token, value)
        files['web/' + target] = text
    files['web/index.html'] = files['web/index.html'].replace('__CONTENT__', files.pop('web/_content'))
    if project:
        files['web/notes-adapter.mjs'] = plain(TEMPLATES / 'notes-adapter.mjs').read_text(encoding='utf-8')
    files['README.md'] = (f'# {name}\n\n'
        f'Fra produktroden: `python -B sdk/check_app.py --app {app_id}`\n\n'
        f'Lokalt med den rigtige projekt- og temabro: `python -B sdk/preview.py --app {app_id}`. '
        'Prøven bruger midlertidige syntetiske projektdata og starter ingen køer.\n\n'
        'Appen har intet bundlingtrin: `web/` er pakkens færdige statiske filer. '
        'Se `docs/NY-APP-GUIDE.md` om pakke, signeret katalog og installation.\n')
    target = apps / app_id
    target.mkdir()  # Exclusive directory creation; never merge into another app.
    written = []
    try:
        (target / 'web').mkdir()
        for relative, text in files.items():
            destination = plain(target / relative)
            with destination.open('x', encoding='utf-8', newline='\n') as stream:
                stream.write(text)
            written.append(destination)
    except BaseException:
        # Remove only the files we created, never an existing/foreign tree.
        for file in reversed(written):
            plain(file).unlink()
        (target / 'web').rmdir()
        target.rmdir()
        raise
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--id', required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--port', required=True, type=int)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--project', action='store_true')
    mode.add_argument('--stateless', action='store_true')
    args = parser.parse_args(argv)
    try:
        target = create_app(args.id, args.name, args.port, project=args.project)
    except (ValueError, OSError) as error:
        parser.exit(2, str(error) + '\n')
    print(json.dumps({'created': str(target), 'project': args.project}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
