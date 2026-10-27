"""Persistent simulation uses authoritative queues, never printer/laser hardware."""
import importlib.util
import json
import http.client
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest import mock
import uuid

from app.demo_workshop import setup, demo_root, demo_component, demo_printers
from app.instance import InstanceLock
from scripts.smoke_slice import cube

ACTOR = {'id': 'teacher:' + 'd' * 32, 'role': 'teacher', 'name': 'Demolærer'}
SVG = '<svg xmlns="http://www.w3.org/2000/svg" data-laservaerksted="1" width="20mm" height="20mm" viewBox="0 0 20 20"><g data-operation="cut"><path data-operation="cut" d="M1 1H19V19H1Z"/></g></svg>'


def envelope(action, params, version):
    return {'action': action, 'params': params, 'expected_version': version, 'request_id': uuid.uuid4().hex}


class DemoWorkshopTests(unittest.TestCase):
    def test_setup_is_explicit_idempotent_preserves_jobs_and_has_no_network(self):
        from app.laser_server import Inbox
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'demo'
            with mock.patch.object(socket.socket, 'connect', side_effect=AssertionError('No network in setup')):
                result = setup(root)
                self.assertTrue(demo_root(root))
                self.assertEqual(result['laser_station_id'], 'demo-laser')
                settings = (root / 'printvaerksted/settings.json').read_bytes()
                inbox = Inbox(root / 'designvaerksted')
                job = inbox.submit({'elevnavn': 'DEMO-elev', 'klasse': 'DEMO', 'projekt': 'DEMO-test',
                                    'requestId': uuid.uuid4().hex, 'svg': SVG})
                self.assertEqual(setup(root), result)
                self.assertEqual(settings, (root / 'printvaerksted/settings.json').read_bytes())
                self.assertEqual(Inbox(root / 'designvaerksted').jobs()[0]['id'], job['id'])
            self.assertEqual(demo_component(root / 'printvaerksted'), 'print')
            with InstanceLock(root):
                with self.assertRaises(RuntimeError):
                    setup(root)

    def test_unmarked_existing_data_invalid_marker_and_real_connection_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            settings = root / 'settings.json'
            settings.write_text('private production data')
            with self.assertRaisesRegex(ValueError, 'ny, tom'):
                setup(root)
            self.assertEqual(settings.read_text(), 'private production data')
            (root / 'demo-component.json').write_text('{"mode":"simulation"}')
            with self.assertRaises(ValueError):
                demo_component(root)
            with self.assertRaises(ValueError):
                demo_printers({'printers': [{'id': 'demo-mini', 'model': 'A1 mini', 'host': '192.168.1.40'}]})

    def test_real_print_queue_simulates_pins_workflow_restarts_and_requires_clear(self):
        import app.server as module
        from app.worker import Worker, DemoPrinter
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'demo'
            setup(root)
            with mock.patch.object(module, 'BambuPrinter', side_effect=AssertionError('No Bambu driver')), \
                 mock.patch.object(module, 'Cameras', side_effect=AssertionError('No camera')), \
                 mock.patch.object(socket.socket, 'connect', side_effect=AssertionError('No network in simulation')), \
                 mock.patch('app.slicer.Slicer.slice', side_effect=AssertionError('No physical slicing')):
                app = module.Application(root / 'printvaerksted')
                try:
                    app.worker.stop()
                    app.worker = None
                    self.assertTrue(app.demo)
                    self.assertIsInstance(app.drivers['demo-mini'], DemoPrinter)
                    self.assertIn('DEMO', app.printer_bridge.snapshot()['printers'][0]['name'])
                    job = app.queue.submit(cube(20), 'demo.stl', '#3678DC', uuid.uuid4().hex)
                    app.queue.reserve()
                    pinned = app.queue.get_job(job['id'])['workflow']
                    for _ in range(3):
                        current = app.queue.get_job(job['id'])
                        definition = json.loads(current['workflow'])['definition']
                        step = definition['steps'][current['workflow_step']]
                        app.queue.advance_workflow(ACTOR['id'], envelope('printer.job.advance',
                            {'id': job['id'], 'step_id': step['id'], 'confirmed': True}, current['workflow_version']))
                    reserved = app.queue.reserve()
                    Worker(app.queue, app.slicer, app.drivers).process(reserved)
                    self.assertEqual(app.queue.get_job(job['id'])['status'], 'printing')
                    app.shutdown()
                    app = module.Application(root / 'printvaerksted')
                    app.worker.stop()
                    app.worker = None
                    self.assertEqual(app.queue.get_job(job['id'])['workflow'], pinned)
                    self.assertEqual(app.drivers['demo-mini'].state, 'RUNNING')
                    app.drivers['demo-mini'].finish()
                    self.assertEqual(app.queue.get_job(job['id'])['status'], 'done')
                    with self.assertRaises(ValueError):
                        app.queue.release_bed('demo-mini')
                    for _ in range(2):
                        current = app.queue.get_job(job['id'])
                        step = json.loads(current['workflow'])['definition']['steps'][current['workflow_step']]
                        app.queue.advance_workflow(ACTOR['id'], envelope('printer.job.advance',
                            {'id': job['id'], 'step_id': step['id'], 'confirmed': True}, current['workflow_version']))
                    self.assertTrue(app.queue.get_job(job['id'])['workflow_complete'])
                    self.assertTrue(app.queue.printers['demo-mini']['bed_clear'])
                    current_config = app.settings['printers'][0]
                    binding = current_config['workflow']
                    app.printer_bridge.execute(ACTOR['id'], envelope('printer.workflow.assign',
                        {'id': 'demo-mini', 'workflow': binding}, current_config['version']))
                    self.assertIsInstance(app.printer_bridge.driver_factory(current_config, app.queue, app.slicer.profiles), DemoPrinter)
                finally:
                    app.shutdown()

    def test_real_laser_queue_simulates_handoff_only_with_validated_marker(self):
        from app.laser_server import Inbox
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'demo'
            setup(root)
            inbox = Inbox(root / 'designvaerksted')
            job = inbox.submit({'elevnavn': 'DEMO-elev', 'klasse': 'DEMO', 'projekt': 'DEMO laser',
                                'requestId': uuid.uuid4().hex, 'svg': SVG})
            inbox.workflows.execute(ACTOR, envelope('laser.job.bind', {'id': job['id'], 'station_id': 'demo-laser'}, 0))
            for _ in range(3):
                state = inbox.workflows.snapshot()['jobs'][0]
                inbox.workflows.execute(ACTOR, envelope('laser.job.advance',
                    {'id': job['id'], 'step_id': state['step']['id'], 'confirmed': True}, state['version']))
            state = inbox.workflows.snapshot()['jobs'][0]
            self.assertTrue(state['production_authorized'])
            self.assertTrue(state['handoff_claimed'])
            # A fresh inbox instance reads the marker and the same queued revision.
            inbox = Inbox(root / 'designvaerksted')
            inbox.workflows.execute(ACTOR, envelope('laser.job.finish', {'id': job['id'], 'confirmed': True}, state['version']))
            for _ in range(2):
                state = inbox.workflows.snapshot()['jobs'][0]
                inbox.workflows.execute(ACTOR, envelope('laser.job.advance',
                    {'id': job['id'], 'step_id': state['step']['id'], 'confirmed': True}, state['version']))
            self.assertEqual(inbox.jobs()[0]['status'], 'done')
            production = Inbox(Path(folder) / 'production')
            self.assertFalse(production.simulation)

    def test_bootstrap_demo_flag_only_for_complete_explicit_root(self):
        source = Path(__file__).resolve().parents[1] / 'windows-elevhub/server/bootstrap.py'
        spec = importlib.util.spec_from_file_location('demo_bootstrap_contract', source)
        bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'demo'
            setup(root)
            config = {'backend_ports': {'print': 31001, 'design': 31002, 'teacher': 31003}}
            commands = bootstrap._commands(Path(folder), root, config)
            self.assertIn('--demo', commands[0][1])
            commands = bootstrap._commands(Path(folder), Path(folder) / 'normal-school', config)
            self.assertNotIn('--demo', commands[0][1])
            with self.assertRaises(ValueError):
                bootstrap._commands(Path(folder), root, {**config, 'external_backends': ['print']})

    def test_installed_demo_start_uses_own_runtime_and_existing_tls_role_config(self):
        import sys
        import app.demo_workshop as module
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'demo'
            setup(root)
            (root / 'konfiguration.json').write_text(json.dumps({'elev_adresse': '192.0.2.40',
                'laerer_adresse': '127.0.0.1', 'laerer_port': 18780}))
            package = Path(folder) / 'package'
            (package / 'server').mkdir(parents=True)
            (package / 'server/bootstrap.py').write_text('# test launcher fixture')
            with mock.patch.object(module, 'ROOT', package), \
                 mock.patch.object(module.subprocess, 'call', return_value=0) as start:
                self.assertEqual(module.main(['start', '--data-root', str(root)]), 0)
            command = start.call_args.args[0]
            self.assertEqual(command[:3], [sys.executable, '-B', str(package / 'server/bootstrap.py')])
            self.assertEqual(command[command.index('--role-ip') + 1], '192.0.2.40')
            self.assertEqual(command[command.index('--teacher-port') + 1], '18780')

    def test_actual_teacher_http_lists_simulations_and_completes_laser_queue(self):
        from app.server import Application, Server as PrintServer, Handler as PrintHandler
        from app.laser_server import create_server
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'demo'
            setup(root)
            from elevhub import Laerer, Server
            from tilstand import Laererkode, Tilstand
            from makerspace_api import MakerspaceService
            from makerspace_printers import PrinterAdministration
            from makerspace_laser_admin import LaserAdministration
            app = Application(root / 'printvaerksted')
            printer = PrintServer(('127.0.0.1', 0), PrintHandler)
            printer.app = app
            laser = create_server(root / 'designvaerksted', port=0, surface='teacher')
            hub = Server(('127.0.0.1', 0), Laerer)
            hub.navn, hub.apps = 'laerer', []
            hub.tilstand = Tilstand(root / 'hub/state', [])
            hub.kode = Laererkode(root / 'hub')
            hub.kode.vaelg('demo-teacher-123')
            hub.makerspace = MakerspaceService(root / 'hub/makerspace.sqlite3',
                printer_administration=PrinterAdministration({'backend': f'127.0.0.1:{printer.server_port}',
                    'key_file': str(root / 'printvaerksted/makerspace-bridge.key')}),
                laser_administration=LaserAdministration({'backend': f'127.0.0.1:{laser.server_port}',
                    'key_file': str(root / 'designvaerksted/makerspace-laser.key')}))
            servers = [printer, laser, hub]
            workers = []
            for server in servers:
                worker = threading.Thread(target=server.serve_forever)
                worker.start()
                workers.append(worker)
            cookie = None
            def call(method, path, body=None):
                connection = http.client.HTTPConnection('127.0.0.1', hub.server_port, timeout=4)
                headers = {'Origin': f'http://127.0.0.1:{hub.server_port}'}
                if cookie:
                    headers['Cookie'] = cookie
                if body is not None:
                    headers['Content-Type'] = 'application/json'
                try:
                    connection.request(method, path, json.dumps(body).encode() if body is not None else None, headers)
                    response = connection.getresponse()
                    status, values, content = response.status, dict(response.getheaders()), json.loads(response.read())
                    self.assertEqual(status, 200, content)
                    return values, content
                finally:
                    connection.close()
            try:
                headers, _ = call('POST', '/api/laerer/login', {'kode': 'demo-teacher-123'})
                cookie = headers['Set-Cookie'].split(';', 1)[0]
                _, state = call('GET', '/api/makerspace/state')
                self.assertIn('DEMO', state['printer_administration']['printers'][0]['name'])
                self.assertIn('DEMO', state['laser_administration']['stations'][0]['name'])
                self.assertEqual(len(state['workflows']), 2)
                job = laser.inbox.submit({'elevnavn': 'DEMO-elev', 'klasse': 'DEMO', 'projekt': 'Lærerens køprøve',
                                         'requestId': uuid.uuid4().hex, 'svg': SVG})
                call('POST', '/api/makerspace/commands', envelope('laser.job.bind',
                    {'id': job['id'], 'station_id': 'demo-laser'}, 0))
                for _ in range(3):
                    _, state = call('GET', '/api/makerspace/state')
                    current = state['laser_administration']['jobs'][0]
                    call('POST', '/api/makerspace/commands', envelope('laser.job.advance',
                        {'id': job['id'], 'step_id': current['step']['id'], 'confirmed': True}, current['version']))
                _, state = call('GET', '/api/makerspace/state')
                current = state['laser_administration']['jobs'][0]
                call('POST', '/api/makerspace/commands', envelope('laser.job.finish',
                    {'id': job['id'], 'confirmed': True}, current['version']))
                for _ in range(2):
                    _, state = call('GET', '/api/makerspace/state')
                    current = state['laser_administration']['jobs'][0]
                    call('POST', '/api/makerspace/commands', envelope('laser.job.advance',
                        {'id': job['id'], 'step_id': current['step']['id'], 'confirmed': True}, current['version']))
                self.assertEqual(laser.inbox.jobs()[0]['status'], 'done')
            finally:
                for server, worker in zip(servers, workers):
                    server.shutdown()
                    worker.join()
                    server.server_close()
                app.shutdown()
