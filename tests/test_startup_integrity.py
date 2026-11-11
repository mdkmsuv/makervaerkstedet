"""Pilot startup health checks through the exact PowerShell function, without services."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from app import generation_runtime as integrity
from app import generation_host
from tests import test_generation_embedded as embedded


ROOT = Path(__file__).resolve().parents[1]
STARTER = ROOT / 'linux-elevhub/testmiljoe/vaert/start-server-test.ps1'


class _Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == '/api/health' else 404)
        self.end_headers()
        self.wfile.write(b'OK')

    def log_message(self, *_args):
        pass


@unittest.skipUnless(os.name == 'nt' and shutil.which('powershell.exe'),
                     'requires Windows PowerShell 5.1 native argument handling')
class StartupHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='startup-health-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _ps(self, url, ca='', owner=True):
        # Execute the parsed function itself, without dot-sourcing startup code.
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        script = (
            "$tokens=$null; $errors=$null\n"
            "$ast=[Management.Automation.Language.Parser]::ParseFile(" + quote(STARTER) + ",[ref]$tokens,[ref]$errors)\n"
            "if($errors.Count){throw 'Parser error'}\n"
            "$function=@($ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Wait-TestHealth'},$true))\n"
            "if($function.Count -ne 1){throw 'Function missing'}\n"
            "Invoke-Expression $function[0].Extent.Text\n"
            "function Get-OwnedTestProcesses { if(" + ('$true' if owner else '$false') + "){[pscustomobject]@{ProcessId=1}} }\n"
            "$definition=[pscustomobject]@{Navn='Test';Tjek=" + quote(url) +
            ";HealthPython=" + quote(sys.executable) + ";CaPath=" + quote(ca) + "}\n"
            "Wait-TestHealth -Definition $definition -Seconds 1\n"
            "'HEALTH_OK'\n"
        )
        encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
        return subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                               '-EncodedCommand', encoded], capture_output=True, text=True, timeout=15)

    def _server(self, context=None):
        server = ThreadingHTTPServer(('127.0.0.1', 0), _Health)
        if context is not None:
            server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(thread.join, 5)
        return server.server_port

    def test_http_health_passes_nonempty_native_ca_placeholder(self):
        port = self._server()
        result = self._ps(f'http://127.0.0.1:{port}/api/health')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('HEALTH_OK', result.stdout)

    def test_https_health_validates_local_ca(self):
        try:
            from cryptography import x509
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.x509.oid import NameOID
        except ImportError:
            self.skipTest('cryptography is required for local TLS fixture')
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]), critical=False)
                .sign(key, hashes.SHA256()))
        cert_path, key_path = self.root / 'server.crt', self.root / 'server.key'
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                               serialization.PrivateFormat.TraditionalOpenSSL,
                                               serialization.NoEncryption()))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_path, key_path)
        port = self._server(context)
        url = f'https://localhost:{port}/api/health'
        accepted = self._ps(url, cert_path)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertIn('HEALTH_OK', accepted.stdout)
        rejected = self._ps(url, self.root / 'missing-ca.crt')
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('Sidste healthcheck', rejected.stderr)

    def test_dead_process_reports_initial_failure(self):
        result = self._ps('http://127.0.0.1:1/api/health', owner=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('afsluttede under opstart', result.stderr)


@unittest.skipUnless(os.name == 'nt', 'Windows sharing leases and named pipes')
class GenerationIntegrityProofTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='generation-proof-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / 'package'
        self.stable = self.root / 'stable'
        self.package.mkdir()
        (self.stable / 'runtime').mkdir(parents=True)
        (self.package / 'app').mkdir()
        for name in integrity.START_FILES:
            path = self.stable / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'fixed source')
        (self.package / 'app/source.py').write_bytes(b'original')
        (self.stable / 'runtime/python.exe').write_bytes(b'fixed runtime')
        self.manifest = 'a' * 64

    def _child(self, environment=None, *, mock_trusted_parent=True):
        script = (
            'import json,sys\n'
            'from app import generation_runtime as g\n'
            'from pathlib import Path\n'
            'p,s=map(Path,sys.argv[1:3])\n'
            + ('g._trusted_server_process=lambda *args: True\n' if mock_trusted_parent else '') +
            'with g.IntegrityLease(p,s) as own:\n'
            ' print(json.dumps({"locked":own.live,"proof":own.live and '
            'g._child_proof(p,s,sys.argv[3],"print",None)}))\n'
        )
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        if environment is not None:
            env.update(environment)
        result = subprocess.run([sys._base_executable, '-B', '-c', script, str(self.package),
                                 str(self.stable), self.manifest], cwd=self.root,
                                env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_live_parent_proof_and_child_lease(self):
        with integrity.IntegrityLease(self.package, self.stable) as lease:
            self.assertTrue(lease.live)
            with mock.patch.object(integrity, '_namespace_acl_is_private', return_value=True):
                lease.start_proofs(self.manifest)
            self.assertIsNotNone(lease.listener)
            with mock.patch.object(integrity, '_hash', wraps=integrity._hash) as hashed:
                self.assertEqual(self._child(), {'locked': True, 'proof': True})
                self.assertEqual(hashed.call_count, 0)
            # A second reader can enter, but an existing source cannot be
            # rewritten even with its original size and modification time.
            source = self.package / 'app/source.py'
            with self.assertRaises(OSError):
                source.write_bytes(b'changed!')
            runtime = self.stable / 'runtime/python.exe'
            with self.assertRaises(OSError):
                runtime.write_bytes(b'fixed runtime')

    def test_namespace_addition_invalidates_parent_proof(self):
        with integrity.IntegrityLease(self.package, self.stable) as lease:
            with mock.patch.object(integrity, '_namespace_acl_is_private', return_value=True):
                lease.start_proofs(self.manifest)
            # Directory sharing alone permits this: the snapshot check must
            # reject the new importable file before a child can use the proof.
            (self.package / 'app/shadow.py').write_bytes(b'raise RuntimeError()')
            self.assertEqual(self._child(), {'locked': True, 'proof': False})

    def test_unprivileged_fake_parent_cannot_attest(self):
        with integrity.IntegrityLease(self.package, self.stable) as lease:
            with mock.patch.object(integrity, '_namespace_acl_is_private', return_value=True):
                lease.start_proofs(self.manifest)
            self.assertEqual(self._child(mock_trusted_parent=False), {'locked': True, 'proof': False})

    def test_attestation_accepts_only_fixed_local_service_server_command(self):
        executable = str(self.stable / 'runtime/python.exe')
        command = subprocess.list2cmdline([
            executable, '-B', '-I', str(self.stable / integrity.LAUNCHER),
            '--package-root', str(self.package), '--runtime-root', str(self.stable),
            '--manifest-sha256', self.manifest, '--entry', 'server', '--'])
        row = {'pid': os.getpid(), 'sid': 'S-1-5-19', 'executable': executable,
               'command': command}
        response = SimpleNamespace(returncode=0, stdout=json.dumps([row, row]))
        native = ('S-1-5-19', integrity._plain(self.stable / 'runtime/python.exe', False))
        with (mock.patch.object(integrity, '_native_process_identity', return_value=native),
              mock.patch('subprocess.run', return_value=response)):
            self.assertTrue(integrity._trusted_server_process(os.getpid(), self.package,
                                                               self.stable, self.manifest, False))
            forged = dict(row, command=subprocess.list2cmdline([executable, '-c', 'print(1)']))
            response.stdout = json.dumps([forged, row])
            self.assertFalse(integrity._trusted_server_process(os.getpid(), self.package,
                                                                self.stable, self.manifest, False))
            response.stdout = json.dumps([dict(row, sid='S-1-5-21-123-456-789-1001'), row])
            self.assertFalse(integrity._trusted_server_process(os.getpid(), self.package,
                                                                self.stable, self.manifest, False))

    def test_active_attestation_matches_launcher_and_service_commands(self):
        executable = str(self.stable / 'runtime/python.exe')
        selected = 'b' * 64
        installation = 'c' * 32
        row = {'pid': os.getpid(), 'sid': 'S-1-5-19', 'executable': executable,
               'command': ''}
        response = SimpleNamespace(returncode=0, stdout='')

        def attests(arguments):
            row['command'] = subprocess.list2cmdline(arguments)
            response.stdout = json.dumps([row, row])
            native = ('S-1-5-19', integrity._plain(self.stable / 'runtime/python.exe', False))
            with (mock.patch.object(integrity, '_native_process_identity', return_value=native),
                  mock.patch('subprocess.run', return_value=response)):
                return integrity._trusted_server_process(os.getpid(), self.package,
                                                         self.stable, self.manifest, True,
                                                         selected, installation)

        # build_active_command is the local active launcher contract.
        basic = integrity.build_active_command(self.stable, 'server', [])
        self.assertTrue(attests(basic))
        self.assertTrue(attests(integrity.build_active_command(self.stable, 'server', [], selected)))
        self.assertFalse(attests(integrity.build_active_command(self.stable, 'server', [], 'd' * 64)))

        # Get-GenerationCommand in generation-startup.ps1 and generation_host._command
        # place the installed owner's ID before the optional selected hash.
        service = basic[:7] + ['--expected-installation-id', installation] + basic[7:]
        self.assertTrue(attests(service))
        service_selected = service[:9] + ['--expected-selection', selected] + service[9:]
        self.assertTrue(attests(service_selected))
        # The host preflight builds the same service command independently.
        row['command'] = subprocess.list2cmdline([executable]) + ' ' + generation_host._command(
            str(self.stable), installation, 'server', [])
        response.stdout = json.dumps([row, row])
        native = ('S-1-5-19', integrity._plain(self.stable / 'runtime/python.exe', False))
        with (mock.patch.object(integrity, '_native_process_identity', return_value=native),
              mock.patch('subprocess.run', return_value=response)):
            self.assertTrue(integrity._trusted_server_process(os.getpid(), self.package,
                                                               self.stable, self.manifest, True,
                                                               selected, installation))
        changed_owner = service.copy()
        changed_owner[8] = 'd' * 32
        self.assertFalse(attests(changed_owner))
        changed_selection = service_selected.copy()
        changed_selection[10] = 'd' * 64
        self.assertFalse(attests(changed_selection))
        self.assertFalse(attests(service_selected[:11] + ['--unknown', 'x'] + service_selected[11:]))

    def test_forged_systemroot_cannot_choose_attestation_or_acl_executable(self):
        fake_windows = self.root / 'fake-windows'
        fake_shell = fake_windows / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        fake_shell.parent.mkdir(parents=True)
        fake_shell.write_bytes(b'not a Windows system executable')
        executable = str(self.stable / 'runtime/python.exe')
        command = subprocess.list2cmdline([
            executable, '-B', '-I', str(self.stable / integrity.LAUNCHER),
            '--package-root', str(self.package), '--runtime-root', str(self.stable),
            '--manifest-sha256', self.manifest, '--entry', 'server', '--'])
        # Even a forged JSON answer must still pass the separate SID check.
        user_row = {'pid': os.getpid(), 'sid': 'S-1-5-21-123-456-789-1001',
                    'executable': executable, 'command': command}
        calls = []

        def fake_response(arguments, **kwargs):
            calls.append((arguments, kwargs))
            if len(calls) == 1:
                return SimpleNamespace(returncode=0, stdout=json.dumps([user_row, user_row]))
            return SimpleNamespace(returncode=1, stdout='')

        native_shell, native_windows = integrity._native_powershell()
        native = ('S-1-5-19', integrity._plain(self.stable / 'runtime/python.exe', False))
        with (mock.patch.object(integrity, '_native_process_identity', return_value=native),
              mock.patch.dict(os.environ, {'SystemRoot': str(fake_windows),
                                        'WINDIR': str(fake_windows),
                                        'PSMODULEPATH': str(fake_windows),
                                        'COR_ENABLE_PROFILING': '1',
                                        'Cor_Profiler_Path': str(fake_shell),
                                        'CORECLR_PROFILER': 'attacker',
                                        'DOTNET_STARTUP_HOOKS': str(fake_shell),
                                        'COMPlus_ProfAPI_ProfilerCompatibilitySetting': '1'}),
              mock.patch('subprocess.run', side_effect=fake_response)):
            self.assertFalse(integrity._trusted_server_process(os.getpid(), self.package,
                                                                self.stable, self.manifest, False))
            self.assertFalse(integrity._namespace_acl_is_private(
                self.package, self.stable, (({}, set()), ({}, set()), ())))
        self.assertEqual(len(calls), 2)
        for arguments, kwargs in calls:
            self.assertEqual(Path(arguments[0]), native_shell)
            self.assertNotEqual(Path(arguments[0]), fake_shell)
            script = base64.b64decode(arguments[-1]).decode('utf-16le')
            self.assertIn("$PSModuleAutoLoadingPreference='None'", script)
            self.assertIn("$env:PSModulePath=[IO.Path]::Combine($PSHOME,'Modules')", script)
            self.assertEqual(kwargs['env']['SystemRoot'], str(native_windows))
            self.assertEqual(kwargs['env']['windir'], str(native_windows))
            self.assertEqual(kwargs['env']['PSModulePath'], str(native_shell.parent / 'Modules'))
            self.assertEqual(sum(key.casefold() == 'systemroot' for key in kwargs['env']), 1)
            self.assertEqual(sum(key.casefold() == 'windir' for key in kwargs['env']), 1)
            self.assertEqual(sum(key.casefold() == 'psmodulepath' for key in kwargs['env']), 1)
            self.assertFalse(any(key.casefold().startswith(
                ('cor_', 'coreclr_', 'dotnet_', 'complus_')) for key in kwargs['env']))

    @unittest.skipUnless(os.name == 'nt', 'Windows token identity')
    def test_native_identity_reads_current_token_and_actual_image(self):
        sid, image = integrity._native_process_identity(os.getpid())
        self.assertTrue(sid.startswith('S-1-5-'))
        self.assertEqual(image, integrity._plain(Path(sys._base_executable), False))

    def test_native_identity_rejects_forged_powershell_attestation(self):
        expected = integrity._plain(self.stable / 'runtime/python.exe', False)
        caller = ('S-1-5-21-123-456-789-1001', expected)
        with (mock.patch.object(integrity, '_native_process_identity', return_value=caller),
              mock.patch('subprocess.run') as shell):
            self.assertFalse(integrity._trusted_server_process(os.getpid(), self.package,
                                                                self.stable, self.manifest, False))
            self.assertFalse(integrity._namespace_acl_is_private(
                self.package, self.stable, (({}, set()), ({}, set()), ())))
            shell.assert_not_called()
        with (mock.patch.object(integrity, '_native_process_identity',
                                return_value=('S-1-5-19', expected.with_suffix('.attacker'))),
              mock.patch('subprocess.run') as shell):
            self.assertFalse(integrity._trusted_server_process(os.getpid(), self.package,
                                                                self.stable, self.manifest, False))
            shell.assert_not_called()

    def test_bootstrap_uses_only_live_verified_lease_to_avoid_second_scan(self):
        spec = importlib.util.spec_from_file_location('startup_test_bootstrap',
                                                     ROOT / 'windows-elevhub/server/bootstrap.py')
        bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap)
        (self.package / 'runtime').mkdir()
        (self.package / 'runtime/python.exe').write_bytes(b'fixture')
        (self.package / 'web').mkdir()
        (self.package / 'hub').mkdir()
        (self.package / 'hub/elevhub.py').write_bytes(b'pass\n')
        (self.package / 'hub/konfiguration.windows.json').write_text(
            json.dumps({'elev_adresse': bootstrap.ROLE_IP, 'apps': [],
                        'hub_port': 8773, 'laerer_port': 8780}), encoding='utf-8')
        (self.package / 'server').mkdir()
        (self.package / 'server/provision.py').write_bytes(b'pass\n')
        with (mock.patch.object(integrity, 'server_integrity_lease', return_value=True),
              mock.patch.object(integrity, 'validate_generation') as scan):
            self.assertTrue(bootstrap.check(self.package, self.root, runtime_root=self.stable,
                                            manifest_sha256=self.manifest)['ok'])
            scan.assert_not_called()
        with (mock.patch.object(integrity, 'server_integrity_lease', return_value=False),
              mock.patch.object(integrity, 'validate_generation') as scan):
            bootstrap.check(self.package, self.root, runtime_root=self.stable,
                            manifest_sha256=self.manifest)
            scan.assert_called_once()

    def test_stale_or_forged_parent_proof_falls_back(self):
        with integrity.IntegrityLease(self.package, self.stable) as lease:
            with mock.patch.object(integrity, '_namespace_acl_is_private', return_value=True):
                lease.start_proofs(self.manifest)
            payload = json.loads(os.environ['MAKERSPACE_INTEGRITY_PROOF'])
            payload['birth'] += 1
            self.assertEqual(self._child({'MAKERSPACE_INTEGRITY_PROOF': json.dumps(payload)}),
                             {'locked': True, 'proof': False})
            payload['birth'] -= 1
            payload['selection'] = 'b' * 64
            self.assertEqual(self._child({'MAKERSPACE_INTEGRITY_PROOF': json.dumps(payload)}),
                             {'locked': True, 'proof': False})

    def test_writable_namespace_never_uses_fast_path(self):
        self.assertFalse(integrity._private_roots(self.package, self.stable))
        with integrity.IntegrityLease(self.package, self.stable) as lease:
            self.assertTrue(lease.live)
            self.assertFalse(integrity._namespace_acl_is_private(self.package, self.stable,
                                                                  lease.snapshot))
            lease.start_proofs(self.manifest)
            self.assertIsNone(lease.listener)

    def test_acl_gate_allows_only_inert_creator_owner_template(self):
        template = {'sid': 'S-1-3-0', 'mask': 0x1f01ff, 'type': 'Allow', 'inherit_only': True}

        def collect(_command, **kwargs):
            paths = json.loads(kwargs['input'])
            rows = [{'path': path, 'reparse': False, 'owner': 'S-1-5-32-544',
                     'dacl': True, 'bad': False,
                     'rules': [{'sid': 'S-1-5-32-545', 'mask': 0x20089,
                                'type': 'Allow', 'inherit_only': False}, template]}
                    for path in paths]
            return SimpleNamespace(returncode=0, stdout=json.dumps(rows), stderr='')

        native = ('S-1-5-19', integrity._plain(self.stable / 'runtime/python.exe', False))
        with (mock.patch.object(integrity, '_native_process_identity', return_value=native),
              mock.patch('subprocess.run', side_effect=collect)):
            self.assertTrue(integrity._private_roots(self.package, self.stable))
            template['inherit_only'] = False
            self.assertFalse(integrity._private_roots(self.package, self.stable))

    def test_parent_exit_invalidates_proof_before_child_runs(self):
        output = self.root / 'child-result.json'
        child_script = (
            'import json,sys,time\n'
            'from pathlib import Path\n'
            'from app import generation_runtime as g\n'
            'g._trusted_server_process=lambda *args: True\n'
            'p,s,out=map(Path,sys.argv[1:4])\n'
            'time.sleep(0.7)\n'
            "out.write_text(json.dumps(g._child_proof(p,s,sys.argv[4],'print',None)))\n"
        )
        parent_script = (
            'import os,subprocess,sys\n'
            'from pathlib import Path\n'
            'from unittest import mock\n'
            'from app import generation_runtime as g\n'
            'p,s,out=map(Path,sys.argv[1:4])\n'
            'with g.IntegrityLease(p,s) as lease:\n'
            ' with mock.patch.object(g,"_namespace_acl_is_private",return_value=True):\n'
            f'  lease.start_proofs({self.manifest!r})\n'
            f' child=subprocess.Popen([sys._base_executable,"-B","-c",{child_script!r},str(p),str(s),str(out),{self.manifest!r}],'
            f'env=dict(os.environ,PYTHONPATH={str(ROOT)!r}),stdin=subprocess.DEVNULL,'
            'stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n'
            " out.with_suffix('.pid').write_text(str(child.pid))\n"
            ' os._exit(0)\n'
        )
        result = subprocess.run([sys._base_executable, '-B', '-c', parent_script,
                                 str(self.package), str(self.stable), str(output)],
                                cwd=self.root, env=dict(os.environ, PYTHONPATH=str(ROOT)),
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        deadline = time.monotonic() + 10
        while not output.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertTrue(output.exists(), 'child did not report after parent exit')
        self.assertFalse(json.loads(output.read_text(encoding='utf-8')))
        # Wait for the detached test child to exit before removing its cwd.
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        process = kernel.OpenProcess(0x100000, False, int(output.with_suffix('.pid').read_text()))
        if process:
            kernel.WaitForSingleObject(process, 5000)
            kernel.CloseHandle(process)

    def test_stalled_pipe_attempt_is_bounded_and_falls_back(self):
        with mock.patch.object(integrity, '_child_proof_exchange',
                               side_effect=lambda *_args: (time.sleep(0.3) or True)):
            started = time.monotonic()
            self.assertFalse(integrity._child_proof(self.package, self.stable,
                                                     self.manifest, 'print', None,
                                                     timeout=0.05))
            self.assertLess(time.monotonic() - started, 0.2)


@unittest.skipUnless(os.name == 'nt' and embedded.ARCHIVE.is_file(),
                     'requires cached isolated embedded Python')
class SelectedRuntimeFallbackTests(unittest.TestCase):
    def setUp(self):
        self.fixture = embedded.EmbeddedGenerationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.package, self.stable = self.fixture.package, self.fixture.stable
        (self.package / 'app/laser_server.py').write_text("print('SELECTED_START_OK')\n", encoding='utf-8')
        self._seal()

    def _seal(self):
        path = self.package / integrity.MANIFEST
        value = json.loads(path.read_text(encoding='utf-8'))
        value['files'] = {
            item.relative_to(self.package).as_posix(): {
                'size': item.stat().st_size, 'sha256': hashlib.sha256(item.read_bytes()).hexdigest()}
            for item in self.package.rglob('*') if item.is_file() and item != path}
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        path.write_bytes(raw)
        self.digest = hashlib.sha256(raw).hexdigest()

    def _run(self):
        command = integrity.build_command(self.package, self.stable, self.digest, 'design', [])
        fake = {'pipe': r'\\.\pipe\missing-generation-proof', 'key': base64.b64encode(b'x' * 32).decode(),
                'pid': os.getpid(), 'birth': integrity._process_birth(os.getpid()),
                'manifest': self.digest, 'selection': None}
        env = dict(os.environ, MAKERSPACE_INTEGRITY_PROOF=json.dumps(fake))
        return subprocess.run(command, cwd=self.fixture.root, env=env,
                              capture_output=True, text=True, timeout=30)

    def test_stale_proof_forces_full_hash_and_detects_same_metadata_source_tamper(self):
        baseline = self._run()
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        self.assertIn('SELECTED_START_OK', baseline.stdout)
        source = self.package / 'app/laser_server.py'
        before = source.stat()
        source.write_bytes(source.read_bytes().replace(b'SELECTED_START_OK', b'SELECTED_START_NO'))
        os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(source.stat().st_size, before.st_size)
        result = self._run()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('SELECTED_START_OK', result.stdout)

    def test_stale_proof_forces_full_fixed_runtime_inventory(self):
        extra = self.stable / 'runtime/Lib/site-packages/shadow.py'
        extra.write_bytes(b'raise RuntimeError("shadow")\n')
        result = self._run()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('SELECTED_START_OK', result.stdout)

    def test_full_validation_runs_while_immutable_lease_is_held(self):
        with integrity.IntegrityLease(self.package, self.stable) as lease:
            self.assertTrue(lease.live)
            with mock.patch.object(integrity, '_hash', wraps=integrity._hash) as hashed:
                result = integrity.validate_generation(self.package, self.stable, self.digest, 'design')
            self.assertEqual(result['manifest_sha256'], self.digest)
            records = json.loads((self.package / integrity.MANIFEST).read_text(encoding='utf-8'))['files']
            expected = (len(records) + len(integrity._inventory(self.stable / 'runtime')[0])
                        + 2 * len(integrity.START_FILES) + 1)
            self.assertEqual(hashed.call_count, expected)
            lease.assert_unchanged()


if __name__ == '__main__':
    unittest.main()
