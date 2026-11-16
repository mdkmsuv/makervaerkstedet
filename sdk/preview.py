#!/usr/bin/env python3
"""Temporary loopback preview using the real host/theme/project bridge.

A queue app runs behind the real local gateway with no school connection:
valid submissions land in a temporary outbox and are never forwarded.
"""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading

ROOT = Path(__file__).absolute().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'system'), str(ROOT / 'system/linux-elevhub/projekter')]
from sdk.check_app import check_app
from lokal import make_server
from projektarkiv import Projektarkiv
from workshop_sessions import WorkshopSessions
from app_host import uses_gateway
from urllib.parse import urlsplit

RUNTIME = {'service': 'sdk-preview', 'release_id': None}


def offline_gateway(folder, app, shelf_port, shared):
    """The installed gateway handler with queues that can only fail offline."""
    from app import laser_local as gateway

    class NoSchool:
        def _submit(self, data):
            raise gateway.Offline(gateway.OFFLINE)

        def health(self):
            return 503, {'surface': 'student', 'inbox': False}

    class Laser(NoSchool, gateway.ForwardingInbox):
        pass

    class Printing(NoSchool, gateway.PrintForwardingInbox):
        pass

    class NoLibrary:
        def status(self, *_):
            raise OSError('sdk_preview_has_no_svg_library')
        catalogue_response = svg_response = status

    # The address is never contacted: both inboxes fail before any request.
    laser = Laser('http://127.0.0.1:9', outbox=gateway.Outbox(folder / 'outbox'))
    printing = Printing('http://127.0.0.1:9', outbox=gateway.Outbox(
        folder / 'outbox/print', validator=gateway.validate_print_submission))
    servers = gateway.create_installed_servers(
        [app], laser, printing, runtime=RUNTIME, projects_port=shelf_port,
        data_root=folder / 'empty-profile', shared_root=shared, library_client=NoLibrary())
    if not servers:
        raise OSError('sdk_preview_port_unavailable')
    return servers[0]


@contextmanager
def preview_server(source_root, app_id):
    with tempfile.TemporaryDirectory(prefix='mkv-sdk-preview-') as temporary:
        folder = Path(temporary)
        spec = check_app(source_root, app_id, folder / 'inputs')
        descriptor = spec['app_descriptor']
        for item in spec['files']:
            destination = folder / 'installed' / item['path']
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(item['source'], destination)
        app = {**descriptor, 'port': urlsplit(descriptor['start']).port,
               'assets': str(folder / 'installed/usr/share/makervaerkstedet/apps' / app_id / 'www')}
        store = Projektarkiv(folder / 'projects', apps=[app])
        sessions = WorkshopSessions(kinds={app_id} if descriptor['project'] else set())
        common = dict(apps=[app], workshops={app_id: descriptor['start']}, sessions=sessions,
                      runtime=RUNTIME, data_root=folder / 'empty-profile', shared_root=ROOT / 'system/web')
        shelf = None
        if uses_gateway(descriptor):
            # Installed queue apps reach projects through their own gateway.
            shelf = make_server(0, store, **common)
            shelf.home_url = descriptor['start']
            threading.Thread(target=shelf.serve_forever, daemon=True).start()
            try:
                server = offline_gateway(folder, app, shelf.server_port, ROOT / 'system/web')
            except BaseException:
                shelf.shutdown(); shelf.server_close()
                raise
            server.projekter, server.outbox = store, folder / 'outbox'
        else:
            server = make_server(app['port'], store, app=app_id, assets=app['assets'], descriptor=app, **common)
            # The embedded project picker returns to this preview, not a real pupil service.
            server.home_url = descriptor['start']
        try:
            yield server
        finally:
            server.server_close()
            if shelf is not None:
                shelf.shutdown(); shelf.server_close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', required=True)
    parser.add_argument('--source-root', type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        with preview_server(args.source_root, args.app) as server:
            print(json.dumps({'preview': f'http://127.0.0.1:{server.server_port}/', 'temporary_projects': True,
                              'offline_queue': hasattr(server, 'outbox')}), flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    except (ValueError, OSError) as error:
        parser.exit(2, str(error) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
