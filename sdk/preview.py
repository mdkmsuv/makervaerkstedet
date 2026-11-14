#!/usr/bin/env python3
"""Temporary loopback preview using the real host/theme/project bridge; no queues."""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).absolute().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'system'), str(ROOT / 'system/linux-elevhub/projekter')]
from sdk.check_app import check_app
from lokal import make_server
from projektarkiv import Projektarkiv
from workshop_sessions import WorkshopSessions
from app_host import uses_gateway
from urllib.parse import urlsplit


@contextmanager
def preview_server(source_root, app_id):
    with tempfile.TemporaryDirectory(prefix='mkv-sdk-preview-') as temporary:
        folder = Path(temporary)
        spec = check_app(source_root, app_id, folder / 'inputs')
        descriptor = spec['app_descriptor']
        if uses_gateway(descriptor):
            raise ValueError('sdk_preview_has_no_queue_gateway')
        for item in spec['files']:
            destination = folder / 'installed' / item['path']
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(item['source'], destination)
        app = {**descriptor, 'port': urlsplit(descriptor['start']).port,
               'assets': str(folder / 'installed/usr/share/makervaerkstedet/apps' / app_id / 'www')}
        store = Projektarkiv(folder / 'projects', apps=[app])
        sessions = WorkshopSessions(kinds={app_id} if descriptor['project'] else set())
        server = make_server(app['port'], store, app=app_id, assets=app['assets'], apps=[app],
                             workshops={app_id: descriptor['start']}, sessions=sessions,
                             runtime={'service': 'sdk-preview', 'release_id': None}, descriptor=app,
                             data_root=folder / 'empty-profile', shared_root=ROOT / 'system/web')
        # The embedded project picker returns to this preview, not a real pupil service.
        server.home_url = descriptor['start']
        try:
            yield server
        finally:
            server.server_close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', required=True)
    parser.add_argument('--source-root', type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        with preview_server(args.source_root, args.app) as server:
            print(json.dumps({'preview': f'http://127.0.0.1:{server.server_port}/', 'temporary_projects': True}), flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    except (ValueError, OSError) as error:
        parser.exit(2, str(error) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
