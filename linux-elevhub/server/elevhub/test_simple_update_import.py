"""Local fixtures only: no GitHub contact, GPG claims, apt or installation."""
from datetime import datetime, timezone
import gzip
import hashlib
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import threading
import unittest
from unittest.mock import patch
import uuid
import zipfile

import simple_update_import as imp
import test_simple_updates as fixtures

SPEC = importlib.util.spec_from_file_location('reviewed_feed_input', fixtures.SPEC.origin.replace('simple_release.py', 'simple_feed.py'))
FEED = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(FEED)


class ImportFixture(fixtures.Fixture):
    def initialize_import(self):
        self.initialize()
        self.config['catalog'] = []
        self.config['source'] = {'enabled': True, 'refresh_seconds': 60}
        self.feed_crypto = patch.object(FEED, '_minisign')
        self.feed_crypto.start(); self.addCleanup(self.feed_crypto.stop)
        self.responses, self.requests = {}, []
        self.sequence = 0
        self.sign = lambda raw: b'synthetic minisign signature'
        self.importer = imp.UpdateImporter(self.service, opener=self.open, feed_verifier=FEED.verify_descriptor)
        self.service.source_worker = self.importer
        self.addCleanup(self.importer.stop)
        self.build_source()

    def open(self, url, repository):
        self.requests.append((url, repository))
        self.assertEqual(repository, imp.REPOSITORY)
        if url not in self.responses:
            raise AssertionError('Unexpected URL; network access forbidden: ' + url)
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return io.BytesIO(response)

    def build_source(self, *, version='2.0.0', sequence=None, mutate=None):
        self.sequence = sequence if sequence is not None else self.sequence + 1
        artifact = ('synthetic deb bytes ' + version).encode()
        self.component = {'schema': 1, 'repository': imp.REPOSITORY, 'role': 'student', 'platform': 'debian-amd64',
            'component': 'studentcore', 'version': version, 'sequence': self.sequence,
            'artifacts': [{'name': 'elev_' + version + '.deb', 'package': 'makervaerkstedet-elev', 'version': version,
                          'architecture': 'all', 'sha256': hashlib.sha256(artifact).hexdigest(), 'size': len(artifact)}]}
        raw = json.dumps(self.component).encode(); digest = hashlib.sha256(raw).hexdigest()
        packages = ('synthetic Packages ' + version).encode()
        files = {'Packages': packages, 'Packages.gz': gzip.compress(packages, mtime=0),
            'Release': ('synthetic Release ' + version).encode(), 'InRelease': ('synthetic InRelease ' + version).encode(),
            'Release.gpg': b'synthetic GPG signature, not cryptographically tested',
            'pool/' + self.component['artifacts'][0]['name']: artifact,
            'by-hash/SHA256/' + hashlib.sha256(packages).hexdigest(): packages,
            'manifests/' + digest + '.json': raw, 'manifests/' + digest + '.minisig': self.sign(raw)}
        if mutate:
            mutate(files)
        inventory = {'schema': 1, 'files': {name: {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)} for name, data in files.items()}}
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, data in files.items():
                archive.writestr(name, data)
            archive.writestr('snapshot-inventory.json', json.dumps(inventory))
        snapshot = stream.getvalue()
        tag = 'v' + version
        self.feed = {'schema': 1, 'repository': imp.REPOSITORY, 'release_tag': tag, 'sequence': self.sequence,
            'snapshot': {'name': imp.SNAPSHOT, 'sha256': hashlib.sha256(snapshot).hexdigest(), 'size': len(snapshot)}}
        feed_raw = json.dumps(self.feed).encode()
        prefix = 'https://github.com/' + imp.REPOSITORY + '/releases/download/' + tag + '/'
        self.snapshot_url = prefix + imp.SNAPSHOT
        self.responses = {'https://api.github.com/repos/' + imp.REPOSITORY + '/releases/latest':
            json.dumps({'tag_name': tag, 'assets': [{'browser_download_url': 'https://untrusted.invalid/ignored'}]}).encode(),
            prefix + imp.DESCRIPTOR: feed_raw, prefix + imp.DESCRIPTOR + '.minisig': self.sign(feed_raw),
            self.snapshot_url: snapshot}
        self.component_hash = digest
        return files

    def current(self):
        return {name: (self.service.cache / name).read_bytes() for name in imp.ROOT_FILES} | {
            'catalog': self.service.catalog_path.read_bytes()}


class ImportTests(ImportFixture, unittest.TestCase):
    def setUp(self):
        self.initialize_import()

    def test_verified_import_enables_desired_and_keeps_program_data_untouched(self):
        data = self.root / 'queue.sqlite3'; data.write_bytes(b'existing queue and history')
        self.assertTrue(self.importer.refresh())
        self.assertEqual(self.importer.status()['state'], 'ready')
        self.assertEqual(self.service.overview()['catalog'][0]['manifest_sha256'], self.component_hash)
        self.service.order(self.order(self.component_hash))
        desired = self.service.poll('pc1', self.poll())['desired'][0]
        self.assertEqual(desired['packages'][0]['package'], 'makervaerkstedet-elev')
        self.assertEqual(data.read_bytes(), b'existing queue and history')
        self.assertTrue(all(repo == imp.REPOSITORY and not url.startswith('https://untrusted') for url, repo in self.requests))

    def test_failed_download_preserves_old_cache_and_highwater_never_rewinds(self):
        self.assertTrue(self.importer.refresh()); before = self.current()
        self.build_source(version='2.1.0', sequence=5)
        self.responses[self.snapshot_url] = OSError('synthetic disconnection')
        self.assertFalse(self.importer.refresh())
        self.assertEqual(self.current(), before)
        self.assertEqual((self.importer.status()['seen_sequence'], self.importer.status()['active_sequence']), (5, 1))
        self.build_source(version='2.0.0', sequence=1)
        self.assertFalse(self.importer.refresh())
        self.assertEqual(self.importer.status()['code'], 'feed_rollback_rejected')
        self.assertEqual(self.current(), before)

    def test_feed_fork_and_snapshot_tamper_fail_before_publication(self):
        self.assertTrue(self.importer.refresh()); before = self.current()
        self.build_source(version='2.1.0', sequence=1)
        self.assertFalse(self.importer.refresh())
        self.assertEqual(self.importer.status()['code'], 'feed_sequence_collision')
        self.build_source(version='2.3.0', sequence=3)
        self.responses[self.snapshot_url] += b'tamper'
        self.assertFalse(self.importer.refresh())
        self.assertEqual(self.importer.status()['code'], 'update_download_too_large')
        self.assertEqual(self.current(), before)

    def test_strict_zip_paths_links_duplicate_names_and_inventory(self):
        for name in ('../security/enheder.json', '/etc/passwd', 'pool/a/../../bad.deb', 'pool/nested/a.deb', 'identity.key', 'C:/bad.deb'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                imp.member_name(name)
        for label, mutate in [('symlink', lambda info: setattr(info, 'external_attr', (stat.S_IFLNK | 0o777) << 16)),
                              ('duplicate', None)]:
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as archive:
                info = zipfile.ZipInfo('pool/file.deb')
                if mutate:
                    mutate(info)
                archive.writestr(info, b'fake')
                if label == 'duplicate':
                    archive.writestr('pool/FILE.deb', b'duplicate')
            archive_path = self.root / (label + '.zip'); archive_path.write_bytes(stream.getvalue())
            destination = self.root / ('unpack-' + label); destination.mkdir()
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, 'unsafe_snapshot_entry'):
                self.importer._unpack(archive_path, destination)
        self.build_source(mutate=lambda files: files.update({'pool/wrong.deb': b'not a listed component'}))
        # Extra repository packages are publisher-authorized by the signed
        # snapshot. They are not arbitrary installable component choices.
        self.assertTrue(self.importer.refresh())

    def test_component_manifest_must_match_signed_artifact_and_filename(self):
        self.build_source(mutate=lambda files: files.update({'pool/elev_2.0.0.deb': b'changed package'}))
        self.assertFalse(self.importer.refresh())
        self.assertEqual(self.importer.status()['code'], 'component_artifact_not_in_snapshot')
        self.assertFalse(self.service.catalog_path.exists())

    def test_snapshot_inventory_hash_and_file_set_are_enforced(self):
        for index, mutation in enumerate(('wrong_hash', 'missing_entry')):
            self.build_source(version='2.' + str(index) + '.1')
            with zipfile.ZipFile(io.BytesIO(self.responses[self.snapshot_url])) as archive:
                files = {name: archive.read(name) for name in archive.namelist()}
            inventory = json.loads(files['snapshot-inventory.json'])
            if mutation == 'wrong_hash':
                inventory['files']['Packages']['sha256'] = '0' * 64
            else:
                del inventory['files']['Packages']
            files['snapshot-inventory.json'] = json.dumps(inventory).encode()
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as archive:
                for name, data in files.items():
                    archive.writestr(name, data)
            snapshot = stream.getvalue()
            self.feed['snapshot'].update(sha256=hashlib.sha256(snapshot).hexdigest(), size=len(snapshot))
            descriptor = next(url for url in self.responses if url.endswith('/' + imp.DESCRIPTOR))
            raw = json.dumps(self.feed).encode()
            self.responses.update({self.snapshot_url: snapshot, descriptor: raw, descriptor + '.minisig': self.sign(raw)})
            self.assertFalse(self.importer.refresh())
            self.assertEqual(self.importer.status()['code'], 'snapshot_member_hash_mismatch' if mutation == 'wrong_hash' else 'snapshot_inventory_mismatch')
        self.assertFalse(self.service.catalog_path.exists())

    def test_old_feed_remains_usable_after_payload_transfer(self):
        def slow_payload(url, repository):
            if url == self.snapshot_url:
                self.now = datetime(2028, 1, 1, tzinfo=timezone.utc)
            return self.open(url, repository)
        self.importer.opener = slow_payload
        self.assertTrue(self.importer.refresh(), self.importer.status())
        self.service.order(self.order(self.component_hash))
        self.assertEqual(self.service.poll('pc1', self.poll())['desired'][0]['manifest_sha256'], self.component_hash)

    def test_partial_publication_rolls_back_metadata_catalog_and_keeps_immutables(self):
        self.assertTrue(self.importer.refresh()); before = self.current()
        for fail_after in ('Packages', 'InRelease'):
            self.build_source(version='2.' + str(self.sequence + 1) + '.0')
            def interrupted(name):
                if name == fail_after:
                    raise OSError('synthetic publication interruption')
            self.importer.publication_hook = interrupted
            self.assertFalse(self.importer.refresh())
            self.assertEqual(self.current(), before)
            self.assertFalse(self.importer.backup.exists())
        self.importer.publication_hook = lambda name: None
        self.assertTrue(self.importer.refresh())
        self.assertEqual(self.importer.status()['state'], 'ready')

    def test_immutable_collision_and_stage_change_are_not_resealed(self):
        self.assertTrue(self.importer.refresh()); before = self.current()
        pool = self.service.cache / 'pool/elev_2.0.0.deb'; pool.write_bytes(b'changed old immutable')
        self.assertFalse(self.importer.refresh())
        self.assertEqual(self.importer.status()['code'], 'immutable_cache_collision')
        pool.write_bytes(('synthetic deb bytes 2.0.0').encode())
        original = self.importer._publish
        def changed_stage(stage, files, descriptors, feed):
            (stage / 'pool/elev_2.0.0.deb').write_bytes(b'changed after verification')
            return original(stage, files, descriptors, feed)
        with patch.object(self.importer, '_publish', side_effect=changed_stage):
            self.assertFalse(self.importer.refresh())
        self.assertEqual(self.current(), before)

    def test_recovery_restores_previous_metadata_without_network(self):
        self.assertTrue(self.importer.refresh()); before = self.current()
        backup = self.importer.backup; backup.mkdir()
        for name in imp.ROOT_FILES:
            shutil.copyfile(self.service.cache / name, backup / name)
        shutil.copyfile(self.service.catalog_path, backup / 'catalog-before.json')
        self.importer._journal({'committed': False, 'metadata': dict.fromkeys(imp.ROOT_FILES, True), 'catalog': True})
        (self.service.cache / 'Packages').write_bytes(b'partially replaced index')
        (self.service.cache / 'InRelease').write_bytes(b'partially replaced release')
        self.requests.clear()
        reopened = imp.UpdateImporter(self.service, opener=self.open, feed_verifier=FEED.verify_descriptor)
        self.addCleanup(reopened.stop)
        self.assertEqual(self.current(), before)
        self.assertEqual(self.requests, [])

    def test_refresh_ids_worker_lock_and_automatic_start(self):
        body = {'command_id': str(uuid.uuid4())}
        self.assertEqual(self.importer.request_refresh(body), self.importer.request_refresh(body))
        self.assertEqual(self.requests, [])
        self.importer.run_lock.acquire()
        try:
            self.assertFalse(self.importer.refresh())
        finally:
            self.importer.run_lock.release()
        done = threading.Event()
        self.importer.publication_hook = lambda name: done.set() if name == 'InRelease' else None
        self.importer.start()
        self.assertTrue(done.wait(5), 'automatic startup import did not publish')
        self.importer.stop()
        self.assertEqual(self.importer.status()['active_sequence'], 1)


@unittest.skipUnless(fixtures.tls_fixture.OPENSSL, 'existing TLS fixture requires OpenSSL')
class ImportHTTPTests(fixtures.UpdateHTTPTests):
    # Only this route test, not the parent class's already covered tests.
    test_teacher_credentials_origin_and_device_root_separation = None
    test_public_cache_get_head_traversal_and_post = None
    test_bind_requires_teacher_origin_and_cannot_be_done_by_device = None

    def test_refresh_status_teacher_origin_and_no_browser_url(self):
        self.config['source'] = {'enabled': True, 'refresh_seconds': 60}
        worker = imp.UpdateImporter(self.service, opener=lambda *_: self.fail('request thread accessed GitHub'))
        self.service.source_worker = worker
        self.addCleanup(worker.stop)
        body = {'command_id': str(uuid.uuid4())}
        self.assertEqual(self.request(1, '/api/admin/updates/source')[0], 401)
        self.assertEqual(self.request(1, '/api/admin/updates/refresh', body, {'Cookie': self.cookie})[0], 403)
        origin = 'https://localhost:' + str(self.teacher.server_port)
        headers = {'Cookie': self.cookie, 'Origin': origin}
        one = self.request(1, '/api/admin/updates/refresh', body, headers)
        two = self.request(1, '/api/admin/updates/refresh', body, headers)
        self.assertEqual((one[0], two[0]), (202, 202))
        self.assertEqual(json.loads(one[1]), json.loads(two[1]))
        self.assertEqual(self.request(1, '/api/admin/updates/refresh', body | {'url': 'https://evil.invalid'}, headers)[0], 400)
        self.assertEqual(self.request(1, '/api/admin/updates/source', headers=headers)[0], 200)
        self.assertEqual(self.service.overview()['revision'], 0)


@unittest.skipUnless(os.environ.get('MKV_MINISIGN_EXE') and os.environ.get('MKV_MINISIGN_SHA256'), 'approved real minisign tool/pin not supplied')
class ActualImportTests(ImportFixture, unittest.TestCase):
    def setUp(self):
        self.initialize_import()
        self.crypto.stop(); self.feed_crypto.stop()
        self.tool = Path(os.environ['MKV_MINISIGN_EXE'])
        self.assertEqual(hashlib.sha256(self.tool.read_bytes()).hexdigest(), os.environ['MKV_MINISIGN_SHA256'])
        self.assertNotIn('onedrive', str(self.root.resolve()).lower())
        self.private, public = self.root / 'synthetic.key', self.root / 'synthetic.pub'
        subprocess.run([str(self.tool), '-G', '-W', '-s', str(self.private), '-p', str(public)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        self.config.update(minisign_exe=str(self.tool), minisign_sha256=os.environ['MKV_MINISIGN_SHA256'], public_key=public.read_text().splitlines()[-1])
        def sign(raw):
            path = self.root / (uuid.uuid4().hex + '.json'); path.write_bytes(raw)
            subprocess.run([str(self.tool), '-S', '-s', str(self.private), '-m', str(path)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            return path.with_suffix('.json.minisig').read_bytes()
        self.sign = sign
        self.build_source(sequence=1)

    def test_actual_signed_feed_component_and_cached_device_offer(self):
        self.importer.feed_verifier = None  # Exercise the production fixed-file loader too.
        self.now = datetime(2028, 1, 1, tzinfo=timezone.utc)  # Signed more than a year earlier.
        self.assertTrue(self.importer.refresh(), self.importer.status())
        self.service.order(self.order(self.component_hash))
        self.assertEqual(self.service.poll('pc1', self.poll())['desired'][0]['manifest_sha256'], self.component_hash)
        self.build_source(version='2.1.0', sequence=2)
        descriptor = next(url for url in self.responses if url.endswith('/' + imp.DESCRIPTOR))
        self.responses[descriptor] += b' '
        self.assertFalse(self.importer.refresh())
        self.assertEqual(self.importer.status()['code'], 'invalid_signature')
        self.assertEqual(self.importer.status()['active_sequence'], 1)
        self.build_source(version='2.2.0', sequence=3)
        self.assertTrue(self.importer.refresh(), self.importer.status())
        self.build_source(version='2.1.0', sequence=2)
        self.assertFalse(self.importer.refresh())
        self.assertEqual(self.importer.status()['code'], 'feed_rollback_rejected')
        self.assertEqual(self.importer.status()['active_sequence'], 3)


if __name__ == '__main__':
    unittest.main()
