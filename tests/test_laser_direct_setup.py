from contextlib import redirect_stdout
from io import StringIO
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from app.laser_transfer import FluxTransfer, TransferError, _ManagedGhost
from tests.test_laser_transfer import FakeWebSocket


UUID = 'a' * 32
KEY = '-----BEGIN RSA PRIVATE KEY-----\nfixture-secret\n'
SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'probe_laser_direct.py'
spec = importlib.util.spec_from_file_location('probe_laser_direct', SCRIPT)
probe_script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe_script)


class DiscoverySocket(FakeWebSocket):
    def recv(self):
        return 'text', json.dumps(next(self.responses))


class DirectSetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.store = self.folder / 'Beam Studio' / 'config.json'
        self.store.parent.mkdir()
        self.store.write_text(json.dumps({'flux-rsa-key': KEY, 'other': 42}), encoding='utf-8')
        self.executable = self.folder / 'flux_api.exe'
        self.executable.write_bytes(b'fixture only')
        self.config = {'managed_ghost': True, 'flux_api': str(self.executable),
                       'client_key_source': 'beam_studio', 'beam_studio_store': str(self.store),
                       'device_uuid': UUID, 'model': 'fhexa1', 'enabled': False, 'commissioned': False}

    def test_read_only_probe_works_before_enable_and_emits_only_safe_commands(self):
        sockets = []

        def factory(host, port, path, timeout):
            if path == '/ws/discover':
                ws = DiscoverySocket(host, port, path, timeout, [{'status': 'updated', 'uuid': UUID}])
            else:
                ws = FakeWebSocket(host, port, path, timeout,
                                   [{'status': 'connected'}, {'status': 'ok', 'uuid': UUID, 'model': 'fhexa1'},
                                    {'status': 'ok', 'device_status': {'st_id': 0}}])
            sockets.append(ws)
            return ws

        class FakeManaged:
            def __init__(self, executable):
                self.executable = executable
                self.stopped = False
                managed.append(self)

            def start(self):
                return 54321

            def stop(self):
                self.stopped = True

        managed = []
        with patch('app.laser_transfer._ManagedGhost', FakeManaged):
            transfer = FluxTransfer(self.config, websocket_factory=factory)
            self.assertFalse(transfer.status()['available'])
            self.assertEqual(transfer.probe(), {'model': 'fhexa1', 'uuid': UUID, 'idle': True})
        self.assertEqual(sockets[0].address, ('127.0.0.1', 54321, '/ws/discover', 5))
        self.assertEqual(sockets[0].text, [])
        self.assertEqual(sockets[1].address, ('127.0.0.1', 54321, '/ws/control/' + UUID, 10))
        self.assertEqual(sockets[1].text, [KEY, 'deviceinfo', 'play report'])
        self.assertEqual(sockets[1].binary, [])
        self.assertTrue(all(s.closed for s in sockets))
        self.assertTrue(managed[0].stopped)

    def test_managed_probe_discovers_exact_uuid_before_control(self):
        config = dict(self.config)
        sockets = []

        def factory(host, port, path, timeout):
            replies = ([{'status': 'updated', 'uuid': 'c' * 32},
                        {'status': 'updated', 'uuid': UUID}] if path == '/ws/discover' else
                       [{'status': 'connected'}, {'status': 'ok', 'uuid': UUID, 'model': 'fhexa1'},
                        {'status': 'ok', 'device_status': {'st_id': 0}}])
            ws = (DiscoverySocket if path == '/ws/discover' else FakeWebSocket)(host, port, path, timeout, replies)
            sockets.append(ws)
            return ws

        with patch('app.laser_transfer._ManagedGhost') as ghost:
            ghost.return_value.start.return_value = 54321
            result = FluxTransfer(config, websocket_factory=factory).probe()
        self.assertTrue(result['idle'])
        self.assertEqual([s.address[2] for s in sockets], ['/ws/discover', '/ws/control/' + UUID])
        self.assertEqual(sockets[0].text, [])
        self.assertEqual(sockets[1].text, [KEY, 'deviceinfo', 'play report'])
        self.assertTrue(all(s.closed for s in sockets))
        ghost.return_value.stop.assert_called_once()

    def test_bad_store_and_wrong_model_fail_closed_without_key_in_error(self):
        self.store.write_text(json.dumps({'flux-rsa-key': KEY.replace('RSA PRIVATE', 'PUBLIC')}), encoding='utf-8')
        transfer = FluxTransfer(self.config, websocket_factory=lambda *a, **k: self.fail('socket opened'))
        with self.assertRaises(TransferError) as caught:
            transfer.probe()
        self.assertNotIn('fixture-secret', str(caught.exception))
        self.store.write_text(json.dumps({'flux-rsa-key': KEY}), encoding='utf-8')
        responses = [{'status': 'connected'}, {'status': 'ok', 'uuid': UUID, 'model': 'fbb2'}]
        with patch('app.laser_transfer._ManagedGhost') as ghost:
            ghost.return_value.start.return_value = 54321
            def factory(host, port, path, timeout):
                return (DiscoverySocket(host, port, path, timeout, [{'uuid': UUID}]) if path == '/ws/discover'
                        else FakeWebSocket(host, port, path, timeout, responses))
            transfer = FluxTransfer(self.config, websocket_factory=factory)
            with self.assertRaisesRegex(TransferError, 'matcher ikke'):
                transfer.probe()
            ghost.return_value.stop.assert_called_once()

    def test_managed_ghost_is_loopback_only_and_terminated_after_use(self):
        reservation = MagicMock()
        reservation.__enter__.return_value.getsockname.return_value = ('127.0.0.1', 54321)
        process = MagicMock()
        process.poll.side_effect = [None, None]
        with patch('app.laser_transfer.socket.socket', return_value=reservation), \
             patch('app.laser_transfer.socket.create_connection') as connect, \
             patch('app.laser_transfer.subprocess.Popen', return_value=process) as popen:
            connect.return_value.__enter__.return_value = None
            manager = _ManagedGhost(self.executable)
            self.assertEqual(manager.start(), 54321)
            manager.stop()
        args = popen.call_args.args[0]
        self.assertEqual(args[1:4], ['--ip', '127.0.0.1', '--port'])
        self.assertEqual(args[4], '54321')
        self.assertIn('--trace-pid', args)
        self.assertNotIn('--simulate', args)
        self.assertEqual(connect.call_args.args[0], ('127.0.0.1', 54321))
        process.terminate.assert_called_once()

    def test_setup_candidate_preserves_other_config_and_stays_disabled_by_default(self):
        existing = {'server': 'http://localhost:8767', 'vision': {'kind': 'manual'}, 'port': 8790}
        candidate = probe_script.candidate_config(existing, model='fhexa1', device_uuid=UUID,
                                                   flux_api=self.executable, beam_store=self.store)
        self.assertEqual(candidate['server'], existing['server'])
        self.assertEqual(candidate['vision'], existing['vision'])
        self.assertEqual(candidate['machine'], 'fhexa1')
        self.assertFalse(candidate['direct_transfer']['enabled'])
        self.assertFalse(candidate['direct_transfer']['commissioned'])
        self.assertNotIn('flux-rsa-key', json.dumps(candidate))
        with self.assertRaises(ValueError):
            probe_script.candidate_config(dict(existing, machine='fbb2'), model='fhexa1',
                                          device_uuid=UUID, flux_api=self.executable)

    def test_script_writes_only_after_successful_probe_and_keeps_backup(self):
        path = self.folder / 'station.json'
        path.write_text(json.dumps({'server': 'http://localhost:8767', 'machine': 'fhexa1'}), encoding='utf-8')
        argv = ['--config', str(path), '--model', 'fhexa1', '--uuid', UUID,
                '--flux-api', str(self.executable), '--beam-store', str(self.store)]
        with patch.object(probe_script, 'FluxTransfer') as transfer:
            transfer.return_value.probe.return_value = {'model': 'fhexa1', 'uuid': UUID, 'idle': True}
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(probe_script.main(argv), 0)
        saved = json.loads(path.read_text(encoding='utf-8'))
        self.assertFalse(saved['direct_transfer']['enabled'])
        self.assertEqual(saved['server'], 'http://localhost:8767')
        self.assertEqual(len(list(self.folder.glob('station.json.bak-*'))), 1)
        self.assertNotIn('fixture-secret', output.getvalue())
        with patch.object(probe_script, 'FluxTransfer') as transfer:
            transfer.return_value.probe.return_value = {'model': 'fhexa1', 'uuid': UUID, 'idle': False}
            with self.assertRaises(SystemExit):
                probe_script.main(argv + ['--enable'])
        self.assertFalse(json.loads(path.read_text(encoding='utf-8'))['direct_transfer']['enabled'])
        with patch.object(probe_script, 'FluxTransfer') as transfer:
            transfer.return_value.probe.return_value = {'model': 'fhexa1', 'uuid': UUID, 'idle': True}
            with redirect_stdout(StringIO()):
                self.assertEqual(probe_script.main(argv + ['--enable']), 0)
        self.assertTrue(json.loads(path.read_text(encoding='utf-8'))['direct_transfer']['enabled'])


if __name__ == '__main__':
    unittest.main()
