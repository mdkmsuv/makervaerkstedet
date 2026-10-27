"""Actual embedded processes share one program/data selection and lifetime lease."""
import hashlib
import json
import os
from pathlib import Path
import py_compile
import shutil
import subprocess
import time
import unittest

from app.generation_lock import GenerationLock
from app.generation_runtime import build_active_command, build_command
from tests import test_generation_embedded as embedded


@unittest.skipUnless(os.name == 'nt' and embedded.ARCHIVE.is_file(), 'requires cached Windows embedded Python')
class ActiveGenerationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = embedded.EmbeddedGenerationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.program, self.stable = self.fixture.root, self.fixture.package, self.fixture.stable
        self.data = self.fixture._school_data()
        self.control = self.stable / 'control'
        self.control.mkdir()
        self.lock = self.control / 'generation.lock'
        self.lock.write_bytes(b'1')
        self.record = {'format': 1, 'installation_id': 'a' * 32, 'revision': 1,
                       'runtime_root': str(self.stable), 'program_root': str(self.program),
                       'data_root': str(self.data), 'manifest_sha256': self.fixture.manifest_sha256}
        self._select(self.record)

    def _select(self, record):
        raw = json.dumps(record, sort_keys=True).encode('utf-8')
        target = self.control / 'active-generation.json'
        with GenerationLock(self.lock, exclusive=True):
            pending = target.with_suffix('.tmp')
            pending.write_bytes(raw)
            os.replace(pending, target)
        self.selected_hash = hashlib.sha256(raw).hexdigest()

    def _reseal(self, data_contract=None):
        path = self.program / 'makerspace-release.json'
        manifest = json.loads(path.read_text(encoding='utf-8'))
        if data_contract is not None:
            manifest['data_contract'] = data_contract
        manifest['files'] = {p.relative_to(self.program).as_posix(): {
            'size': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in self.program.rglob('*') if p.is_file() and p != path}
        raw = json.dumps(manifest, ensure_ascii=False).encode('utf-8')
        path.write_bytes(raw)
        self.record['manifest_sha256'] = hashlib.sha256(raw).hexdigest()
        self._select(self.record)

    def _run(self, entry, arguments, expected=None):
        return subprocess.run(build_active_command(self.stable, entry, arguments, expected),
                              capture_output=True, text=True, cwd=self.root, timeout=25)

    def test_actual_enrollment_binds_data_and_never_uses_caller_path(self):
        result = self._run('enrollment', ['ticket', '--name', 'pc-01'])
        self.assertEqual(result.returncode, 0, result.stderr)
        path = self.data / 'security/enrollment.json'
        before = path.read_bytes()
        wrong = self.root / 'wrong data'
        wrong.mkdir()
        for option in ('--data-root', '--data-ro'):
            with self.subTest(option=option):
                result = self._run('enrollment', ['ticket', '--name', 'pc-02', option, str(wrong)])
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(list(wrong.iterdir()), [])
        checked = self._run('enrollment', ['check', '--data-root=' + str(self.data)])
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_stable_helper_bytecode_cannot_override_the_checked_source(self):
        # Matching timestamp/size caches would be accepted by normal importlib
        # source loading. The pre-import selector must execute its .py directly.
        for name in ('generation_selection.py', 'generation_lock.py'):
            source = self.stable / 'app' / name
            decoy = self.root / name
            body = b'raise RuntimeError("stale stable helper executed")\n'
            decoy.write_bytes(body + b'#' * (source.stat().st_size - len(body)))
            os.utime(decoy, ns=(source.stat().st_atime_ns, source.stat().st_mtime_ns))
            cache = source.parent / '__pycache__' / (source.stem + '.cpython-314.pyc')
            cache.parent.mkdir(exist_ok=True)
            py_compile.compile(str(decoy), cfile=str(cache), doraise=True,
                               invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
        result = self._run('enrollment', ['check'])
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_incomplete_or_stale_selection_and_manual_bypass_are_rejected(self):
        for action in ('transition', 'restore', 'stale', 'missing'):
            with self.subTest(action=action):
                marker = None
                expected = None
                active = self.control / 'active-generation.json'
                raw = active.read_bytes()
                if action == 'transition':
                    marker = self.control / 'selection-transition.json'
                elif action == 'restore':
                    marker = self.data / '.restore-hold.json'
                elif action == 'stale':
                    expected = self.selected_hash
                    self.record['revision'] += 1
                    self._select(self.record)
                else:
                    active.unlink()
                if marker is not None:
                    marker.write_text('{}')
                try:
                    result = self._run('enrollment', ['ticket', '--name', 'pc-01'], expected)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse((self.data / 'security/enrollment.json').exists())
                    self.assertFalse((self.data / 'security/enrollment').exists())
                    with GenerationLock(self.lock, exclusive=True):
                        pass  # Error paths must release their lease.
                finally:
                    if marker is not None:
                        marker.unlink()
                    active.write_bytes(raw)
        command = build_command(self.program, self.stable, self.fixture.manifest_sha256,
                                'enrollment', ['check', '--data-root', str(self.data)])
        rejected = subprocess.run(command, capture_output=True, text=True, timeout=25)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('manuelt valg', rejected.stderr)

    def _bootstrap_program(self):
        (self.program / 'server').mkdir()
        for name in ('bootstrap.py', 'provision.py'):
            shutil.copy2(embedded.ROOT / 'windows-elevhub/server' / name, self.program / 'server' / name)
        for name in ('instance.py', 'backup_guard.py', 'process_tree.py', 'paths.py'):
            shutil.copy2(embedded.ROOT / 'app' / name, self.program / 'app' / name)
        (self.program / 'hub').mkdir()
        (self.program / 'web').mkdir()
        (self.program / 'hub/elevhub.py').write_text('# not started by --check\n')
        (self.program / 'hub/konfiguration.windows.json').write_text('{}')
        config = {'elev_adresse': '192.0.2.10', 'laerer_adresse': '127.0.0.1',
                  'laerer_port': 8780, 'hub_port': 8773,
                  'tls': {'certifikat': str(self.data / 'security/server.crt'),
                          'noegle': str(self.data / 'security/server.key'), 'ca': str(self.data / 'security/ca.crt')},
                  'enhedsregister': str(self.data / 'security/enheder.json'), 'apps': []}
        (self.data / 'konfiguration.json').write_text(json.dumps(config), encoding='utf-8')
        self._reseal()

    def test_real_bootstrap_check_uses_selected_program_and_data(self):
        self._bootstrap_program()
        result = self._run('server', ['--check', '--installed', '--role-ip', '192.0.2.10',
                                      '--teacher-address', '127.0.0.1'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['roleIp'], '192.0.2.10')
        self.assertFalse((self.data / 'logs').exists())

    def test_real_supervisor_starts_all_children_with_same_selection_and_drains_them(self):
        self._bootstrap_program()
        (self.program / 'installation.json').write_text(json.dumps({'data_root': str(self.root / 'old-data')}))
        children = {'print': 'app/background.py', 'design': 'app/laser_server.py', 'hub': 'hub/elevhub.py'}
        for role, source in children.items():
            (self.program / source).write_text(
                'import json, os, sys, time\nfrom pathlib import Path\nfrom app.paths import data_root\n'
                'args=sys.argv[1:]; data=Path(args[args.index("--data")+1]); root=data.parent\n'
                f'role={role!r}\n'
                '(root/("ready-"+role+".json")).write_text(json.dumps('
                '{"source":__file__,"data":str(data),"default_data":str(data_root()),"exe":sys.executable,"pid":os.getpid()}))\n'
                'deadline=time.monotonic()+15\n'
                'while not (root/("stop-"+role)).exists() and time.monotonic()<deadline: time.sleep(0.02)\n'
                'raise SystemExit(17)\n', encoding='utf-8')
        self._reseal({'hub': 1, 'print': 1, 'laser': 1})
        command = build_active_command(self.stable, 'server', ['--role-ip', '192.0.2.10',
                                                              '--teacher-address', '127.0.0.1'])
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            while not all((self.data / ('ready-' + role + '.json')).exists() for role in children):
                if process.poll() is not None:
                    errors = process.communicate(timeout=5)[1]
                    logs = '\n'.join(p.read_text(errors='replace')[-2000:] for p in (self.data / 'logs').glob('*.log'))
                    self.fail('Supervisor exited before readiness: ' + errors + logs)
                if time.monotonic() >= deadline:
                    self.fail('Supervisor children did not become ready')
                time.sleep(0.02)
            for role, source in children.items():
                value = json.loads((self.data / ('ready-' + role + '.json')).read_text())
                leaf = {'print': 'printvaerksted', 'design': 'designvaerksted', 'hub': 'hub'}[role]
                self.assertEqual(Path(value['data']), self.data / leaf)
                self.assertEqual(Path(value['default_data']), self.data)
                self.assertEqual(Path(value['source']), self.program / source)
                self.assertEqual(Path(value['exe']), self.stable / 'runtime/python.exe')
            with self.assertRaises(RuntimeError):
                GenerationLock(self.lock, exclusive=True)
            (self.data / 'stop-print').write_text('stop fixture')
            _, errors = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 17, errors)
            self.assertFalse((self.root / 'old-data').exists())
            deadline = time.monotonic() + 5
            while True:
                try:
                    with GenerationLock(self.lock, exclusive=True):
                        break
                except RuntimeError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.02)
        finally:
            for role in children:
                (self.data / ('stop-' + role)).write_text('stop fixture')
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=20)

    def test_child_keeps_selection_locked_after_parent_dies(self):
        ready, release = self.root / 'child-ready.json', self.root / 'release-child'
        parent_result = self.root / 'parent-ready.json'
        (self.program / 'app/laser_server.py').write_text(
            'import json, os, subprocess, sys\nfrom pathlib import Path\n'
            'from app import generation_runtime as runtime\n'
            'args=sys.argv[1:]\n'
            'child=subprocess.Popen(runtime.child_command("app.boxes_worker", '
            '["--ready", args[args.index("--ready")+1], "--release", args[args.index("--release")+1]]))\n'
            'Path(args[args.index("--parent-result")+1]).write_text(json.dumps('
            '{"argv":args,"child":child.pid,"source":__file__,"exe":sys.executable}))\n'
            'raise SystemExit(child.wait(timeout=20))\n', encoding='utf-8')
        (self.program / 'app/boxes_worker.py').write_text(
            'import json, sys, time\nfrom pathlib import Path\n'
            'args=sys.argv[1:]; ready=Path(args[args.index("--ready")+1]); release=Path(args[args.index("--release")+1])\n'
            'ready.write_text(json.dumps({"source":__file__,"exe":sys.executable}))\n'
            'deadline=time.monotonic()+15\n'
            'while not release.exists() and time.monotonic()<deadline: time.sleep(0.02)\n', encoding='utf-8')
        self._reseal()
        command = build_active_command(self.stable, 'design', ['--ready', str(ready), '--release', str(release),
                                                              '--parent-result', str(parent_result)])
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            while not (ready.exists() and parent_result.exists()):
                if process.poll() is not None:
                    self.fail('Selected parent exited: ' + process.communicate(timeout=5)[1])
                if time.monotonic() >= deadline:
                    self.fail('Selected child did not become ready')
                time.sleep(0.02)
            parent = json.loads(parent_result.read_text())
            child = json.loads(ready.read_text())
            self.assertEqual(parent['argv'][-2:], ['--data', str(self.data / 'designvaerksted')])
            self.assertEqual(Path(parent['source']), self.program / 'app/laser_server.py')
            self.assertEqual(Path(child['source']), self.program / 'app/boxes_worker.py')
            for result in (parent, child):
                self.assertEqual(Path(result['exe']), self.stable / 'runtime/python.exe')
            with self.assertRaises(RuntimeError):
                GenerationLock(self.lock, exclusive=True)
            process.kill()
            process.wait(timeout=5)
            # The orphaned, already selected child must prevent a switch by itself.
            with self.assertRaises(RuntimeError):
                GenerationLock(self.lock, exclusive=True)
            release.write_bytes(b'1')
            deadline = time.monotonic() + 5
            while True:
                try:
                    with GenerationLock(self.lock, exclusive=True):
                        break
                except RuntimeError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.02)
        finally:
            release.write_bytes(b'1')
            if process.poll() is None:
                process.kill()
            # Child inherits the pipes; communicate joins its bounded exit too.
            process.communicate(timeout=20)


if __name__ == '__main__':
    unittest.main()
