"""Fake-publisher domain tests and real loopback HTTPS auth/cache routes.

Only minisign cryptography is replaced. The shared strict manifest parser runs.
These are source tests, not evidence for a signed release, apt or installation.
"""
from datetime import datetime, timezone
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import subprocess
import shutil
import threading
import unittest
from unittest.mock import patch
import uuid

import elevhub
from sikkerhed import Enhedsregister
from simple_updates import SimpleUpdates, UpdateError, DEBIAN_PACKAGES
import test_sikkerhed as tls_fixture
from tilstand import Laererkode, Tilstand

SPEC = importlib.util.spec_from_file_location('reviewed_release_input', Path(__file__).resolve().parents[3] / 'app/simple_release.py')
SHARED = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SHARED)


class Fixture:
    def initialize(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = datetime(2026, 11, 4, tzinfo=timezone.utc)
        self.tokens = {'root': 'r' * 43, 'ui': 'u' * 43}
        register = self.root / 'registry.json'
        register.write_text(json.dumps({'enheder': {'pc1': {
            role + '_token_sha256' if role == 'ui' else 'token_sha256': hashlib.sha256(token.encode()).hexdigest()
            for role, token in self.tokens.items()}}}))
        self.registry = Enhedsregister(register)
        (self.root / 'public-apt-cache/dists/stable').mkdir(parents=True)
        (self.root / 'public-apt-cache/dists/stable/InRelease').write_bytes(b'synthetic signed-repository-placeholder')
        self.config = {'repository': SHARED.REPOSITORY, 'minisign_exe': 'minisign', 'minisign_sha256': 'a' * 64,
            'public_key': 'fakepublisher', 'apt_cache': 'public-apt-cache', 'catalog': [],
            'device_bindings': {'pc1': {'role': 'student', 'platform': 'debian-amd64'}}}
        self.crypto = patch.object(SHARED, '_minisign')
        self.crypto.start()
        self.addCleanup(self.crypto.stop)
        self.hash = self.release()
        self.service = self.reopen()

    def release(self, version='2.0.0', sequence=2, component='studentcore', package=None):
        name = 'release-' + str(len(self.config['catalog']))
        manifest = {'schema': 1, 'repository': SHARED.REPOSITORY, 'role': 'student', 'platform': 'debian-amd64',
            'component': component, 'version': version, 'sequence': sequence,
            'artifacts': [{'name': name + '.deb', 'package': package or SHARED.DEBIAN_COMPONENTS[component][0],
                'version': version, 'architecture': SHARED.DEBIAN_COMPONENTS[component][1], 'sha256': 'b' * 64, 'size': 10}]}
        raw = json.dumps(manifest).encode()
        digest = hashlib.sha256(raw).hexdigest()
        prefix = 'public-apt-cache/manifests/' + digest
        (self.root / 'public-apt-cache/manifests').mkdir(exist_ok=True)
        (self.root / (prefix + '.json')).write_bytes(raw)
        (self.root / (prefix + '.minisig')).write_bytes(b'fake signature')
        self.config['catalog'].append({'manifest': prefix + '.json', 'signature': prefix + '.minisig',
            'component': component, 'role': 'student', 'platform': 'debian-amd64'})
        return digest

    def reopen(self):
        return SimpleUpdates(self.root, self.config, self.root, self.registry,
                             verifier=SHARED.verify_manifest, clock=lambda: self.now)

    def order(self, digest=None, revision=0, **changes):
        body = {'command_id': str(uuid.uuid4()), 'expected_revision': revision, 'devices': ['pc1'],
            'component': 'studentcore', 'manifest_sha256': digest or self.hash, 'rollback_manifest_sha256': None}
        return body | changes

    def poll(self, **changes):
        return {'schema': 1, 'installed': [], 'result': None} | changes

    def binding_body(self, **changes):
        return {'command_id': str(uuid.uuid4()), 'expected_revision': 0, 'device': 'pc1',
                'role': 'student', 'platform': 'debian-amd64'} | changes


class DesiredVersionTests(Fixture, unittest.TestCase):
    def setUp(self):
        self.initialize()

    def test_command_revision_idempotence_and_restart(self):
        body = self.order()
        receipt = self.service.order(body)
        self.assertEqual(receipt, self.reopen().order(body))
        with self.assertRaisesRegex(UpdateError, 'command_id_conflict'):
            self.service.order(body | {'component': 'scratch'})
        with self.assertRaisesRegex(UpdateError, 'revision_conflict'):
            self.service.order(self.order())
        self.assertEqual(self.reopen().poll('pc1', self.poll())['desired'][0]['manifest_sha256'], self.hash)

    def test_unknown_or_unapproved_package_never_offered(self):
        arbitrary = self.release(package='sudo')
        for digest in ('c' * 64, arbitrary):
            with self.subTest(digest=digest), self.assertRaisesRegex(UpdateError, 'unknown_release'):
                self.service.order(self.order(digest))
        self.service.order(self.order())
        self.now = datetime(2028, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(self.service.poll('pc1', self.poll())['desired'][0]['manifest_sha256'], self.hash)

    def test_offline_device_catches_up_without_poll_mutating_revision(self):
        self.service.order(self.order())
        for _ in range(3):
            result = self.reopen().poll('pc1', self.poll())
            self.assertEqual((result['revision'], len(result['desired'])), (1, 1))
        overview = self.service.overview()
        self.assertEqual(overview['revision'], 1)
        self.assertEqual(overview['devices'][0]['installed'], [])

    def test_reports_do_not_turn_success_into_installed_inventory(self):
        self.service.order(self.order())
        report = self.poll(installed=[{'component': 'studentcore', 'version': '1.0'}],
                           result={'revision': 1, 'state': 'succeeded', 'code': None})
        self.service.poll('pc1', report)
        device = self.reopen().overview()['devices'][0]
        self.assertEqual(device['installed'], report['installed'])
        self.assertEqual(device['installation_evidence'], 'device_report')

    def test_explicit_rollback_and_highwater_survive_restart(self):
        old = self.release(version='1.9', sequence=1)
        self.service.overview()
        with self.assertRaisesRegex(UpdateError, 'explicit_rollback'):
            self.service.order(self.order(old))
        with self.assertRaisesRegex(UpdateError, 'rollback_hash_mismatch'):
            self.service.order(self.order(old, rollback_manifest_sha256=self.hash))
        self.service.order(self.order(old, rollback_manifest_sha256=old))
        self.assertTrue(self.reopen().poll('pc1', self.poll())['desired'][0]['rollback'])
        self.config['catalog'] = self.config['catalog'][1:]
        self.assertTrue(self.reopen().overview()['catalog'][0]['rollback_required'])

    def test_registry_binding_and_rejection_preserve_all_targets(self):
        with self.assertRaisesRegex(UpdateError, 'unknown_update_device'):
            self.service.order(self.order(devices=['pc1', 'unknown']))
        self.assertEqual(self.service.overview()['revision'], 0)
        self.service.order(self.order())
        self.config['device_bindings']['pc1'] = {'role': 'server', 'platform': 'windows-amd64'}
        self.assertEqual(self.reopen().poll('pc1', self.poll())['desired'], [])

    def test_publisher_version_split_or_same_sequence_rewrite_is_blocked(self):
        path = self.root / self.config['catalog'][0]['manifest']
        original = path.read_bytes()
        self.service.overview()  # Durable high-water for the original bytes.
        item = json.loads(original)
        item['version'] = item['artifacts'][0]['version'] = '2.0.1'
        def replace_manifest():
            raw = json.dumps(item).encode()
            prefix = 'public-apt-cache/manifests/' + hashlib.sha256(raw).hexdigest()
            (self.root / (prefix + '.json')).write_bytes(raw)
            (self.root / (prefix + '.minisig')).write_bytes(b'fake signature')
            self.config['catalog'][0].update(manifest=prefix + '.json', signature=prefix + '.minisig')
        replace_manifest()
        self.assertEqual(self.service.overview()['catalog'], [])
        self.assertEqual(self.service.overview()['catalog_errors'][0]['code'], 'release_sequence_collision')
        item['sequence'] = 3
        item['artifacts'][0]['version'] = '1.0'
        replace_manifest()
        self.assertEqual(self.service.overview()['catalog_errors'][0]['code'], 'artifact_version_mismatch')

    def test_manifest_hash_paths_are_content_bound(self):
        self.service.order(self.order())
        desired = self.service.poll('pc1', self.poll())['desired'][0]
        self.assertEqual(desired['manifest_path'], '/apt/manifests/' + self.hash + '.json')
        raw = self.service.apt_file(desired['manifest_path']).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), desired['manifest_sha256'])
        wrong = self.root / self.config['catalog'][0]['manifest']
        item = json.loads(wrong.read_bytes()); item['sequence'] += 1
        wrong.write_text(json.dumps(item))
        self.assertEqual(self.service.poll('pc1', self.poll())['desired'], [])
        self.assertEqual(self.service.overview()['catalog_errors'][0]['code'], 'release_cache_hash_binding_mismatch')

    def test_versions_are_reported_without_a_server_side_ordering_algorithm(self):
        self.service.order(self.order())
        reported = [{'component': 'studentcore', 'version': '3:9.0~beta'}]
        offer = self.service.poll('pc1', self.poll(installed=reported))['desired'][0]
        self.assertEqual(offer['manifest_sha256'], self.hash)
        self.assertEqual(self.service.overview()['devices'][0]['installed'], reported)
        self.assertEqual(DEBIAN_PACKAGES, SHARED.DEBIAN_COMPONENTS)

    def test_windows_release_has_no_central_apply_without_a_windows_client(self):
        item = {'schema': 1, 'repository': SHARED.REPOSITORY, 'role': 'server', 'platform': 'windows-amd64',
            'component': 'server', 'version': '2.0.0', 'sequence': 1,
            'artifacts': [{'name': 'Server-Setup.exe', 'package': None, 'version': '2.0.0', 'architecture': 'amd64', 'sha256': 'b' * 64, 'size': 7}]}
        raw = json.dumps(item).encode(); digest = hashlib.sha256(raw).hexdigest()
        prefix = 'public-apt-cache/manifests/' + digest
        (self.root / (prefix + '.json')).write_bytes(raw)
        (self.root / (prefix + '.minisig')).write_bytes(b'fake publisher signature')
        self.config['catalog'] = [{'manifest': prefix + '.json', 'signature': prefix + '.minisig',
            'component': 'server', 'role': 'server', 'platform': 'windows-amd64'}]
        self.config['device_bindings']['pc1'] = {'role': 'server', 'platform': 'windows-amd64'}
        self.service = self.reopen()
        self.assertFalse(self.service.overview()['catalog'][0]['orderable'])
        with self.assertRaisesRegex(UpdateError, 'windows_update_requires_role_installer'):
            self.service.order(self.order(digest, component='server'))
        with self.service.transaction() as db:
            db.execute('INSERT INTO desired VALUES(?,?,?,?,?)', ('pc1', 'server', digest, 1, 0))
        self.assertEqual(self.service.poll('pc1', self.poll())['desired'], [])

    def test_teacher_binding_survives_restart_and_overrides_bootstrap_config(self):
        self.config['device_bindings'] = {}
        self.service = self.reopen()
        self.assertEqual(self.service.overview()['unbound_devices'], ['pc1'])
        with self.assertRaisesRegex(UpdateError, 'unknown_update_device'):
            self.service.poll('pc1', self.poll())
        body = self.binding_body()
        receipt = self.service.bind(body)
        self.assertEqual(receipt, self.reopen().bind(body))
        self.config['device_bindings']['pc1'] = {'role': 'server', 'platform': 'windows-amd64'}
        self.service = self.reopen()
        self.assertEqual(self.service.overview()['devices'][0]['role'], 'student')
        self.assertEqual(self.service.overview()['unbound_devices'], [])
        self.service.order(self.order(revision=1))
        offer = self.service.poll('pc1', self.poll())['desired'][0]
        self.assertEqual((offer['revision'], offer['manifest_sha256']), (2, self.hash))
        with self.assertRaisesRegex(UpdateError, 'command_id_conflict'):
            self.service.bind(body | {'role': 'server', 'platform': 'windows-amd64'})

    def test_bind_rejects_unknown_revoked_bad_platform_and_stale_revision(self):
        for change in ({'device': 'unknown'}, {'role': 'student', 'platform': 'windows-amd64'}, {'expected_revision': 1}):
            with self.subTest(change=change), self.assertRaises(UpdateError):
                self.service.bind(self.binding_body(**change))
        body = self.binding_body()
        self.service.bind(body)
        (self.root / 'registry.json').write_text(json.dumps({'enheder': {}}))
        with self.assertRaisesRegex(UpdateError, 'unknown_update_device'):
            self.reopen().bind(body)  # Revoked access is checked even on replay.
        self.assertEqual(self.reopen().overview()['devices'], [])

    def test_cache_traversal_directories_and_links_are_rejected(self):
        for raw in ['/apt/../registry.json', '/apt/%2e%2e/registry.json', '/apt//etc/x', '/apt/a\\b', '/apt/dists/stable/']:
            with self.subTest(raw=raw), self.assertRaises(UpdateError):
                self.service.apt_file(raw)
        self.assertEqual(self.service.apt_file('/apt/dists/stable/InRelease').read_bytes(), b'synthetic signed-repository-placeholder')
        link = self.root / 'public-apt-cache/link'
        try:
            link.symlink_to(self.root / 'registry.json')
        except OSError:
            self.skipTest('symlink creation unavailable on this OS account')
        with self.assertRaisesRegex(UpdateError, 'linked_update_path'):
            self.service.apt_file('/apt/link')

    def test_only_literal_dedicated_public_cache_can_be_configured(self):
        for cache in ('security', 'identity', 'data', '.', '../public-apt-cache',
                      str(self.root / 'security'), str(self.root.parent / 'sibling')):
            with self.subTest(cache=cache), self.assertRaisesRegex(UpdateError, 'apt_cache_must_be_public_cache'):
                SimpleUpdates(self.root, self.config | {'apt_cache': cache}, self.root, self.registry)

    def test_windows_packaged_layout_and_empty_catalog_need_no_sys_path_search(self):
        bundle = self.root / 'bundle'
        (bundle / 'hub').mkdir(parents=True)
        source = Path(__file__).with_name('simple_updates.py')
        shutil.copyfile(source, bundle / 'hub/simple_updates.py')
        spec = importlib.util.spec_from_file_location('packaged_simple_updates', bundle / 'hub/simple_updates.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        config = self.config | {'catalog': []}
        service = module.SimpleUpdates(self.root, config, self.root, self.registry)
        self.assertEqual(service.overview()['catalog'], [])
        self.assertIsNone(service.verifier)
        (bundle / 'app').mkdir()
        shutil.copyfile(SPEC.origin, bundle / 'app/simple_release.py')
        service.config['catalog'] = self.config['catalog']
        self.assertEqual(service.overview()['catalog'], [])  # No real verifier binary in this fixture.
        self.assertEqual(Path(service.verifier.__globals__['__file__']), bundle / 'app/simple_release.py')
        module.__file__ = str(self.root / 'unknown-layout/simple_updates.py')
        with self.assertRaisesRegex(ValueError, 'unsupported_release_verifier_layout'):
            module.verifier_path()


@unittest.skipUnless(tls_fixture.OPENSSL, 'existing OpenSSL required for loopback TLS fixture')
class UpdateHTTPTests(Fixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Reuse only certificate setup, not the other test class's test suite.
        tls_fixture.SikkerIntegration.setUpClass()

    @classmethod
    def tearDownClass(cls):
        tls_fixture.SikkerIntegration.tearDownClass()

    def setUp(self):
        self.initialize()
        self.servers = []
        for handler in (elevhub.Hub, elevhub.Laerer):
            server = elevhub.Server(('127.0.0.1', 0), handler, tls=tls_fixture.SikkerIntegration.tls)
            server.simple_updates, server.enheder = self.service, self.registry
            server.navn = 'test-simple-updates'
            server.tilstand = Tilstand(self.root, [])
            server.kode = Laererkode(self.root)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.servers.append((server, thread))
        self.teacher = self.servers[1][0]
        self.teacher.kode.vaelg('synthetic-private-code')
        self.cookie = 'elevhub_laerer=' + self.teacher.kode.log_ind('synthetic-private-code', '127.0.0.1')
        self.addCleanup(self.close_servers)

    def close_servers(self):
        for server, thread in self.servers:
            server.shutdown(); server.server_close(); thread.join()

    def request(self, which, path, body=None, headers=None, method=None):
        server = self.servers[which][0]
        connection = http.client.HTTPSConnection('localhost', server.server_port, context=tls_fixture.SikkerIntegration.client_tls, timeout=5)
        values = {'Content-Type': 'application/json'} | (headers or {})
        connection.request(method or ('POST' if body is not None else 'GET'), path,
                           json.dumps(body) if body is not None else None, values)
        response = connection.getresponse()
        result = (response.status, response.read(), dict(response.getheaders()))
        connection.close()
        return result

    def test_teacher_credentials_origin_and_device_root_separation(self):
        self.assertEqual(self.request(1, '/api/admin/updates')[0], 401)
        self.assertEqual(self.request(1, '/api/admin/updates', self.order(), {'Cookie': self.cookie})[0], 403)
        self.assertEqual(self.request(1, '/api/admin/updates', self.order(), {'Cookie': self.cookie, 'Origin': 'https://foreign.invalid'})[0], 403)
        origin = 'https://localhost:' + str(self.teacher.server_port)
        self.assertEqual(self.request(1, '/api/admin/updates', self.order(), {'Cookie': self.cookie, 'Origin': origin})[0], 200)
        for role, expected in [('ui', 401), ('root', 200)]:
            headers = {'Authorization': 'Bearer ' + self.tokens[role], 'X-Elevhub-Computer': 'pc1'}
            self.assertEqual(self.request(0, '/api/device/updates', self.poll(), headers)[0], expected)
        headers = {'Authorization': 'Bearer ' + self.tokens['root'], 'X-Elevhub-Computer': 'unknown'}
        self.assertEqual(self.request(0, '/api/device/updates', self.poll(), headers)[0], 401)
        headers['X-Elevhub-Computer'] = 'pc1'; headers['Origin'] = 'null'
        self.assertEqual(self.request(0, '/api/device/updates', self.poll(), headers)[0], 403)

    def test_public_cache_get_head_traversal_and_post(self):
        path = '/apt/dists/stable/InRelease'
        self.assertEqual(self.request(0, path)[0:2], (200, b'synthetic signed-repository-placeholder'))
        status, content, headers = self.request(0, path, method='HEAD')
        self.assertEqual((status, content), (200, b''))
        self.assertGreater(int(headers['Content-Length']), 0)
        self.assertEqual(self.request(0, '/apt/%2e%2e/registry.json')[0], 400)
        self.assertEqual(self.request(0, path, {})[0], 405)
        self.assertEqual(self.request(0, '/api/enhed/opdatering')[0], 410)
        self.assertEqual(self.request(1, '/api/updates/feed/state')[0], 410)

    def test_native_apt_flat_suite_prefix_get_head_and_traversal(self):
        payload = b'synthetic flat repository signed index'
        (self.service.cache / 'InRelease').write_bytes(payload)
        for path in ('/apt/InRelease', '/apt/./InRelease'):
            with self.subTest(path=path):
                status, body, headers = self.request(0, path)
                self.assertEqual((status, body), (200, payload))
                self.assertEqual(int(headers['Content-Length']), len(payload))
                self.assertNotEqual(headers.get('Connection'), 'close')
                status, body, headers = self.request(0, path, method='HEAD')
                self.assertEqual((status, body), (200, b''))
                self.assertEqual(int(headers['Content-Length']), len(payload))
        for path in ('/apt/./../registry.json', '/apt/./%2e%2e/registry.json',
                     '/apt/././InRelease', '/apt/.//InRelease', '/apt/./'):
            with self.subTest(path=path):
                self.assertEqual(self.request(0, path)[0], 400)

    def test_bind_requires_teacher_origin_and_cannot_be_done_by_device(self):
        self.config['device_bindings'] = {}
        self.service = self.reopen()
        for server, _ in self.servers:
            server.simple_updates = self.service
        body = self.binding_body()
        self.assertEqual(self.request(1, '/api/admin/updates/bind', body)[0], 401)
        self.assertEqual(self.request(1, '/api/admin/updates/bind', body, {'Cookie': self.cookie, 'Origin': 'https://foreign.invalid'})[0], 403)
        ui = {'Authorization': 'Bearer ' + self.tokens['ui'], 'X-Elevhub-Computer': 'pc1'}
        self.assertEqual(self.request(0, '/api/admin/updates/bind', body, ui)[0], 401)
        root = dict(ui, Authorization='Bearer ' + self.tokens['root'])
        self.assertEqual(self.request(0, '/api/device/updates', body, root)[0], 400)
        origin = 'https://localhost:' + str(self.teacher.server_port)
        status, raw, _ = self.request(1, '/api/admin/updates/bind', body, {'Cookie': self.cookie, 'Origin': origin})
        self.assertEqual((status, json.loads(raw)['revision']), (200, 1))
        self.assertEqual(self.service.poll('pc1', self.poll())['revision'], 1)


@unittest.skipUnless(os.environ.get('MKV_MINISIGN_EXE') and os.environ.get('MKV_MINISIGN_SHA256'),
                     'approved minisign binary/hash not supplied; actual crypto not exercised')
class ActualSignedHTTPTests(UpdateHTTPTests):
    """Real signing + strict verifier + HTTPS desired flow; no apt/install."""
    def setUp(self):
        super().setUp()
        self.crypto.stop()
        self.tool = Path(os.environ['MKV_MINISIGN_EXE'])
        self.pin = os.environ['MKV_MINISIGN_SHA256']
        self.assertEqual(hashlib.sha256(self.tool.read_bytes()).hexdigest(), self.pin)
        self.assertNotIn('onedrive', str(self.root.resolve()).lower())
        self.private, self.public = self.root / 'synthetic.key', self.root / 'synthetic.pub'
        subprocess.run([str(self.tool), '-G', '-W', '-s', str(self.private), '-p', str(self.public)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        self.config.update(minisign_exe=str(self.tool), minisign_sha256=self.pin,
                           public_key=self.public.read_text().splitlines()[-1])
        self.original = json.loads((self.root / self.config['catalog'][0]['manifest']).read_bytes())
        self.hash = self.signed(self.original)

    def signed(self, value, **binding):
        raw = json.dumps(value, indent=2).encode()
        digest = hashlib.sha256(raw).hexdigest()
        prefix = 'public-apt-cache/manifests/' + digest
        path = self.root / (prefix + '.json')
        path.write_bytes(raw)
        subprocess.run([str(self.tool), '-S', '-s', str(self.private), '-m', str(path), '-x', str(self.root / (prefix + '.minisig'))],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        self.config['catalog'] = [{'manifest': prefix + '.json', 'signature': prefix + '.minisig',
            'component': value['component'], 'role': value['role'], 'platform': value['platform']} | binding]
        return digest

    def test_real_signed_old_catalog_binding_and_tamper(self):
        # Load the exact shipped <root>/hub + <root>/app arrangement, then
        # exercise its lazy verifier through the existing teacher HTTPS route.
        bundle = self.root / 'real-bundle'
        (bundle / 'hub').mkdir(parents=True); (bundle / 'app').mkdir()
        shutil.copyfile(Path(__file__).with_name('simple_updates.py'), bundle / 'hub/simple_updates.py')
        shutil.copyfile(SPEC.origin, bundle / 'app/simple_release.py')
        spec = importlib.util.spec_from_file_location('real_packaged_updates', bundle / 'hub/simple_updates.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        self.service = module.SimpleUpdates(self.root, self.config, self.root, self.registry, clock=lambda: self.now)
        for server, _ in self.servers:
            server.simple_updates = self.service
        self.assertEqual(self.request(1, '/api/admin/updates', headers={'Cookie': self.cookie})[0], 200)
        self.assertEqual(Path(self.service.verifier.__globals__['__file__']), bundle / 'app/simple_release.py')
        self.assertEqual(self.service.overview()['catalog'][0]['manifest_sha256'], self.hash)
        value = json.loads(json.dumps(self.original))
        value['artifacts'][0]['package'] = 'sudo'
        self.signed(value)
        self.assertEqual(self.service.overview()['catalog_errors'][0]['code'], 'package_component_mismatch')
        self.signed(self.original, role='server')
        self.assertEqual(self.service.overview()['catalog_errors'][0]['code'], 'role_mismatch')
        self.now = datetime(2028, 1, 1, tzinfo=timezone.utc)
        self.signed(self.original)
        self.assertEqual(self.service.overview()['catalog'][0]['manifest_sha256'], self.hash)
        origin = 'https://localhost:' + str(self.teacher.server_port)
        self.assertEqual(self.request(1, '/api/admin/updates', self.order(), {'Cookie': self.cookie, 'Origin': origin})[0], 200)
        path = self.root / self.config['catalog'][0]['manifest']
        path.write_bytes(path.read_bytes() + b' ')
        self.assertEqual(self.service.overview()['catalog_errors'][0]['code'], 'invalid_signature')
        value = json.loads(json.dumps(self.original))
        value['version'] = value['artifacts'][0]['version'] = '1.9.0'
        value['sequence'] = 1
        digest = self.signed(value)
        with self.assertRaisesRegex(ValueError, 'explicit_rollback_required'):
            self.service.order(self.order(digest, revision=1))
        self.service.order(self.order(digest, revision=1, rollback_manifest_sha256=digest))
        self.assertTrue(self.service.poll('pc1', self.poll())['desired'][0]['rollback'])
        self.assertTrue(self.service.overview()['catalog'][0]['rollback_required'])


if __name__ == '__main__':
    unittest.main()
