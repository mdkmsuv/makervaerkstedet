import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock


SOURCE = Path(__file__).resolve().parents[1] / 'windows-elevhub/server/bootstrap.py'


def load_bootstrap():
    spec = importlib.util.spec_from_file_location('generation_bootstrap_test', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GenerationBootstrapTests(unittest.TestCase):
    def test_all_server_children_bind_same_program_runtime_and_data(self):
        module = load_bootstrap()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package, stable, data = root / 'next', root / 'installed', root / 'data'
            commands = module._commands(package, data, {}, stable, 'a' * 64)
            self.assertEqual([name for name, _ in commands], ['print', 'design', 'hub'])
            for name, command in commands:
                self.assertEqual(command[:4], [str(stable / 'runtime/python.exe'), '-B', '-I',
                                               str(stable / 'app/generation_runtime.py')])
                self.assertEqual(command[command.index('--package-root') + 1], str(package))
                self.assertEqual(command[command.index('--runtime-root') + 1], str(stable))
                self.assertEqual(command[command.index('--manifest-sha256') + 1], 'a' * 64)
                self.assertEqual(command[command.index('--entry') + 1], name)
                arguments = command[command.index('--') + 1:]
                leaf = {'print': 'printvaerksted', 'design': 'designvaerksted', 'hub': 'hub'}[name]
                self.assertEqual(arguments[arguments.index('--data') + 1], str(data / leaf))
                if name == 'hub':
                    self.assertEqual(arguments[arguments.index('--konfiguration') + 1], str(data / 'konfiguration.json'))

    def test_external_ownership_and_backend_ports_survive_generation_selection(self):
        module = load_bootstrap()
        config = {'external_backends': ['print'],
                  'backend_ports': {'print': 18765, 'design': 18766, 'teacher': 18767}}
        commands = module._commands(Path('next'), Path('data'), config, Path('stable'), 'a' * 64)
        self.assertEqual([name for name, _ in commands], ['design', 'hub'])
        self.assertIn('18766', commands[0][1])
        self.assertIn('18767', commands[0][1])

    def test_stale_supervisor_is_rejected_before_touching_data(self):
        module = load_bootstrap()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package, stable = root / 'next', root / 'stable'
            package.mkdir()
            stable.mkdir()
            with mock.patch.object(module, 'InstanceLock') as lock, \
                 mock.patch.object(module, 'write_protocol') as protocol, \
                 mock.patch.object(module.subprocess, 'Popen') as start:
                with self.assertRaisesRegex(ValueError, 'faste startmekanisme'):
                    module.run(package, root / 'untouched-data', runtime_root=stable, manifest_sha256='a' * 64)
                lock.assert_not_called()
                protocol.assert_not_called()
                start.assert_not_called()
            self.assertFalse((root / 'untouched-data').exists())

    def test_cached_old_app_modules_are_rejected_before_data_lock(self):
        module = load_bootstrap()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package, stable = root / 'next', root / 'stable'
            (package / 'server').mkdir(parents=True)
            (package / 'app').mkdir()
            (package / 'server/bootstrap.py').write_bytes(b'fixture')
            (stable / 'runtime').mkdir(parents=True)
            (stable / 'runtime/python.exe').write_bytes(b'fixture')
            with mock.patch.object(module, '__file__', str(package / 'server/bootstrap.py')), \
                 mock.patch.object(module.sys, 'executable', str(stable / 'runtime/python.exe')), \
                 mock.patch.object(module, 'InstanceLock') as lock, \
                 mock.patch.object(module.subprocess, 'Popen') as start:
                with self.assertRaisesRegex(ValueError, 'anden programgeneration'):
                    module.run(package, root / 'untouched-data', runtime_root=stable, manifest_sha256='a' * 64)
                lock.assert_not_called()
                start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
