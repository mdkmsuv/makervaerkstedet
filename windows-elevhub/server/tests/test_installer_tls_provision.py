"""Interrupted first TLS provisioning, using isolated files and real crypto."""
import importlib.util
import json
from pathlib import Path
import socket
import ssl
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import serialization


SOURCE = Path(__file__).resolve().parents[1] / 'provision.py'
spec = importlib.util.spec_from_file_location('installer_tls_provision', SOURCE)
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)


class InstallerTlsProvisionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.material = provision._tls_material('192.0.2.2')

    def setUp(self):
        # These are source/crypto tests on a non-admin host.  The production
        # ACL boundary is explicitly injected, while all filesystem and TLS
        # publication paths remain real.
        self._acl = patch.object(provision, '_restrict_private_path', return_value=None)
        self._acl.start()
        self.addCleanup(self._acl.stop)

    def snapshot(self, root):
        return {str(path.relative_to(root)): path.read_bytes()
                for path in root.rglob('*') if path.is_file()}

    def test_failure_at_each_atomic_write_retries_original_identity(self):
        # Journal, every published TLS file, and completed receipt are separate
        # boundaries. Include failure before publication and after a rename.
        for failure_at in range(1, 7):
            for after_write in (False, True):
                with self.subTest(failure_at=failure_at, after_write=after_write), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    security = root / 'security'
                    (root / 'queue.json').write_bytes(b'preserved queue')
                    writes = 0
                    real_write = provision._atomic_write

                    def interrupted_write(path, text):
                        nonlocal writes
                        writes += 1
                        if writes == failure_at and not after_write:
                            raise OSError('simulated power loss')
                        real_write(path, text)
                        if writes == failure_at and after_write:
                            raise OSError('simulated power loss')

                    with patch.object(provision, '_tls_material', return_value=self.material), patch.object(provision, '_atomic_write', side_effect=interrupted_write):
                        with self.assertRaises(OSError):
                            provision._tls(security, '192.0.2.2', 'owner')
                    before = self.snapshot(root)
                    provision._tls(security, '192.0.2.2', 'owner', resume=True, check=True)
                    self.assertEqual(self.snapshot(root), before)
                    with patch.object(provision, '_tls_material', return_value=self.material):
                        provision._tls(security, '192.0.2.2', 'owner', resume=True)
                    for name, content in self.material.items():
                        self.assertEqual((security / name).read_bytes(), content.encode())
                    receipt = json.loads((security / provision.TLS_JOURNAL).read_text())
                    self.assertEqual(receipt['state'], 'complete')
                    self.assertNotIn('material', receipt)
                    self.assertEqual((root / 'queue.json').read_bytes(), b'preserved queue')
                    before = self.snapshot(root)
                    with patch.object(provision, '_tls_material', side_effect=AssertionError('must not generate')):
                        provision._tls(security, '192.0.2.2', 'owner', resume=True)
                    self.assertEqual(self.snapshot(root), before)

    def staged(self, security):
        real_write = provision._atomic_write

        def stop_after_journal(path, text):
            real_write(path, text)
            raise OSError('stopped after journal')

        with patch.object(provision, '_tls_material', return_value=self.material), patch.object(provision, '_atomic_write', side_effect=stop_after_journal):
            with self.assertRaises(OSError):
                provision._tls(security, '192.0.2.2', 'owner')

    def test_journal_temporary_file_recovers_same_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            security = Path(tmp) / 'security'
            self.staged(security)
            journal = security / provision.TLS_JOURNAL
            journal.replace(journal.with_suffix('.json.tmp'))
            with patch.object(provision, '_tls_material', side_effect=AssertionError('must not generate')):
                provision._tls(security, '192.0.2.2', 'owner', resume=True)
            self.assertEqual((security / 'ca.crt').read_bytes(), self.material['ca.crt'].encode())

    def test_torn_first_journal_and_tls_temporary_write_can_retry(self):
        for name in ('tls-initialization.json.tmp', 'server.key.tmp'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                security = Path(tmp) / 'security'
                if name == 'server.key.tmp':
                    self.staged(security)
                    (security / 'ca.crt').write_bytes(self.material['ca.crt'].encode())
                else:
                    security.mkdir()
                (security / name).write_bytes(b'torn temporary write')
                before = self.snapshot(Path(tmp))
                provision._tls(security, '192.0.2.2', 'owner', resume=True, check=True)
                self.assertEqual(self.snapshot(Path(tmp)), before)
                with patch.object(provision, '_tls_material', return_value=self.material):
                    provision._tls(security, '192.0.2.2', 'owner', resume=True)
                self.assertEqual((security / 'server.key').read_bytes(), self.material['server.key'].encode())

    def test_generation_failure_has_no_exposed_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            security = Path(tmp) / 'security'
            with patch.object(provision, '_tls_material', side_effect=OSError('generation failed')):
                with self.assertRaises(OSError):
                    provision._tls(security, '192.0.2.2', 'owner')
            self.assertFalse(security.exists())
            with patch.object(provision, '_tls_material', return_value=self.material):
                provision._tls(security, '192.0.2.2', 'owner', resume=True)

    def test_corrupt_or_foreign_journal_and_changed_files_never_mutate(self):
        for corrupt in ('owner', 'ip', 'payload', 'file', 'malformed', 'missing-key'):
            with self.subTest(corrupt=corrupt), tempfile.TemporaryDirectory() as tmp:
                security = Path(tmp) / 'security'
                self.staged(security)
                path = security / provision.TLS_JOURNAL
                journal = json.loads(path.read_text())
                if corrupt == 'owner':
                    journal['ownerId'] = 'other'
                elif corrupt == 'ip':
                    journal['serverIp'] = '172.17.1.3'
                elif corrupt == 'payload':
                    journal['material']['server.key'] = 'modified'
                elif corrupt == 'missing-key':
                    del journal['material']['server.key']
                elif corrupt == 'file':
                    (security / 'server.key').write_bytes(b'foreign private key')
                path.write_text('not-json' if corrupt == 'malformed' else json.dumps(journal))
                before = self.snapshot(Path(tmp))
                with self.assertRaises(ValueError):
                    provision._tls(security, '192.0.2.2', 'owner', resume=True)
                self.assertEqual(self.snapshot(Path(tmp)), before)

    def test_complete_retry_retains_enrollment_and_legacy_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            security = Path(tmp) / 'security'
            with patch.object(provision, '_tls_material', return_value=self.material):
                provision._tls(security, '192.0.2.2', 'owner')
            (security / 'enheder.json').write_bytes(b' {"enheder":{"elev-1":{"token_sha256":"retained"}}} ')
            before = self.snapshot(Path(tmp))
            provision._tls(security, '192.0.2.2', 'owner', resume=True)
            self.assertEqual(self.snapshot(Path(tmp)), before)
            (security / provision.TLS_JOURNAL).unlink()
            before = self.snapshot(Path(tmp))
            provision._tls(security, '192.0.2.2', 'owner', resume=True)
            self.assertEqual(self.snapshot(Path(tmp)), before)
            with self.assertRaises(ValueError):
                provision._tls(security, '172.17.1.3', 'owner', resume=True)
            self.assertEqual(self.snapshot(Path(tmp)), before)

    def test_complete_missing_file_and_legacy_partial_require_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            security = Path(tmp) / 'security'
            with patch.object(provision, '_tls_material', return_value=self.material):
                provision._tls(security, '192.0.2.2', 'owner')
            (security / 'server.key').unlink()
            for keep_journal in (True, False):
                if not keep_journal:
                    (security / provision.TLS_JOURNAL).unlink()
                before = self.snapshot(Path(tmp))
                with self.assertRaises(ValueError):
                    provision._tls(security, '192.0.2.2', 'owner', resume=True)
                self.assertEqual(self.snapshot(Path(tmp)), before)

    def test_journal_schema_digest_and_read_bounds_rejected_before_mutation(self):
        for bad in ({'schemaVersion': True}, {'schemaVersion': '1'}, {'extra': 'foreign'},
                    {'sha256': {name: 'A' * 64 for name in provision.TLS_FILES}}):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as tmp:
                security = Path(tmp) / 'security'
                self.staged(security)
                path = security / provision.TLS_JOURNAL
                value = json.loads(path.read_text())
                value.update(bad)
                path.write_text(json.dumps(value))
                before = self.snapshot(Path(tmp))
                with self.assertRaises(ValueError):
                    provision._tls(security, '192.0.2.2', 'owner', resume=True)
                self.assertEqual(self.snapshot(Path(tmp)), before)
        with tempfile.TemporaryDirectory() as tmp:
            security = Path(tmp) / 'security'
            self.staged(security)
            (security / provision.TLS_JOURNAL).write_bytes(b' ' * (provision.TLS_MAX_FILE_BYTES + 1))
            before = self.snapshot(Path(tmp))
            with self.assertRaises(ValueError):
                provision._tls(security, '192.0.2.2', 'owner', resume=True)
            self.assertEqual(self.snapshot(Path(tmp)), before)

    def test_complete_tls_leaves_unrelated_enrollment_directory_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            security = Path(tmp) / 'security'
            with patch.object(provision, '_tls_material', return_value=self.material):
                provision._tls(security, '192.0.2.2', 'owner')
            enrollment = security / 'enrollment'
            enrollment.mkdir()
            (enrollment / 'elev-1.json').write_bytes(b'preserved issued enrollment')
            before = self.snapshot(Path(tmp))
            provision._tls(security, '192.0.2.2', 'owner', resume=True)
            self.assertEqual(self.snapshot(Path(tmp)), before)
            receipt_path = security / provision.TLS_JOURNAL
            receipt = json.loads(receipt_path.read_text())
            receipt['material'] = self.material
            receipt_path.write_text(json.dumps(receipt))
            before = self.snapshot(Path(tmp))
            with self.assertRaises(ValueError):
                provision._tls(security, '192.0.2.2', 'owner', resume=True)
            self.assertEqual(self.snapshot(Path(tmp)), before)

    def test_general_reparse_attribute_rejected(self):
        attributes = SimpleNamespace(st_mode=provision.stat.S_IFREG,
                                     st_file_attributes=provision.stat.FILE_ATTRIBUTE_REPARSE_POINT)
        with patch.object(Path, 'lstat', return_value=attributes):
            with self.assertRaises(ValueError):
                provision._tls_assert_path(Path('synthetic-reparse'))

    def test_leaf_renewal_keeps_ca_and_server_key_and_updates_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            security = Path(tmp) / 'security'
            provision._tls(security, '192.0.2.2', 'owner')
            ca_before = (security / 'ca.crt').read_bytes()
            ca_key_before = (security / 'ca.key').read_bytes()
            server_key_before = (security / 'server.key').read_bytes()
            old_cert = x509.load_pem_x509_certificate((security / 'server.crt').read_bytes())
            provision.renew_tls(security, '192.0.2.2')
            new_cert = x509.load_pem_x509_certificate((security / 'server.crt').read_bytes())
            self.assertNotEqual(old_cert.serial_number, new_cert.serial_number)
            self.assertEqual(ca_before, (security / 'ca.crt').read_bytes())
            self.assertEqual(ca_key_before, (security / 'ca.key').read_bytes())
            self.assertEqual(server_key_before, (security / 'server.key').read_bytes())
            self.assertEqual(new_cert.issuer, old_cert.issuer)
            self.assertEqual(
                new_cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo),
                old_cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo),
            )
            names = new_cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            self.assertIn('192.0.2.2', {str(value) for value in names.get_values_for_type(x509.IPAddress)})
            self.assertIn('127.0.0.1', {str(value) for value in names.get_values_for_type(x509.IPAddress)})
            self.assertIn('localhost', {value.lower() for value in names.get_values_for_type(x509.DNSName)})
            self.assertFalse((security / provision.TLS_RENEWAL_JOURNAL).exists())
            receipt = json.loads((security / provision.TLS_JOURNAL).read_text())
            self.assertEqual(receipt['sha256']['server.crt'], provision._tls_digest((security / 'server.crt').read_text()))
            listener = socket.socket()
            listener.bind(('127.0.0.1', 0))
            listener.listen(1)
            server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server_context.load_cert_chain(security / 'server.crt', security / 'server.key')
            def serve_once():
                raw, _address = listener.accept()
                with server_context.wrap_socket(raw, server_side=True) as connection:
                    self.assertEqual(connection.recv(2), b'ok')
                    connection.sendall(b'ok')
            thread = threading.Thread(target=serve_once)
            thread.start()
            try:
                client_context = ssl.create_default_context(cadata=ca_before.decode('ascii'))
                with socket.create_connection(listener.getsockname()) as raw:
                    with client_context.wrap_socket(raw, server_hostname='192.0.2.2') as connection:
                        connection.sendall(b'ok')
                        self.assertEqual(connection.recv(2), b'ok')
            finally:
                listener.close()
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())

    def test_renewal_retries_after_leaf_publish_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            security = Path(tmp) / 'security'
            provision._tls(security, '192.0.2.2', 'owner')
            real_write = provision._atomic_write_unique

            def stop_after_marker(path, text):
                real_write(path, text)
                if path.name == provision.TLS_RENEWAL_JOURNAL:
                    raise OSError('simulated process stop')

            with patch.object(provision, '_atomic_write_unique', side_effect=stop_after_marker):
                with self.assertRaises(OSError):
                    provision.renew_tls(security, '192.0.2.2')
            old_cert = (security / 'server.crt').read_bytes()
            self.assertTrue((security / provision.TLS_RENEWAL_JOURNAL).exists())
            provision.renew_tls(security, '192.0.2.2')
            self.assertNotEqual(old_cert, (security / 'server.crt').read_bytes())
            self.assertFalse((security / provision.TLS_RENEWAL_JOURNAL).exists())

    def test_legacy_four_file_identity_cannot_renew_without_ca_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            security = Path(tmp) / 'security'
            legacy = {name: value for name, value in self.material.items() if name != provision.TLS_PRIVATE_FILE}
            with patch.object(provision, '_tls_material', return_value=legacy):
                provision._tls(security, '192.0.2.2', 'owner')
            before = self.snapshot(Path(tmp))
            with self.assertRaisesRegex(ValueError, 'CA-nøgle'):
                provision.renew_tls(security, '192.0.2.2')
            self.assertEqual(before, self.snapshot(Path(tmp)))

    def test_completed_renewal_receipt_makes_same_operation_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            security = Path(tmp) / 'security'
            provision._tls(security, '192.0.2.2', 'owner')
            first = provision.renew_tls(security, '192.0.2.2', 'a' * 32)
            published = (security / 'server.crt').read_bytes()
            repeated = provision.renew_tls(security, '192.0.2.2', 'a' * 32)
            self.assertEqual(first, repeated)
            self.assertEqual(published, (security / 'server.crt').read_bytes())
            provision.renew_tls(security, '192.0.2.2', 'b' * 32)
            self.assertNotEqual(published, (security / 'server.crt').read_bytes())

    def test_registry_mutation_and_owned_sentinels_survive_renewal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            security = root / 'security'
            provision._tls(security, '192.0.2.2', 'owner')
            registry = json.loads((security / 'enheder.json').read_text())
            registry['enheder']['pilot'] = {'token_sha256': 'a' * 64, 'ui_token_sha256': 'b' * 64}
            (security / 'enheder.json').write_text(json.dumps(registry))
            ssh = root / 'admin-ssh' / 'id_ed25519'
            printer = root / 'printvaerksted' / 'printer-sentinel.json'
            ssh.parent.mkdir(); printer.parent.mkdir()
            ssh.write_bytes(b'private ssh sentinel'); printer.write_bytes(b'printer sentinel')
            provision.renew_tls(security, '192.0.2.2')
            self.assertEqual(json.loads((security / 'enheder.json').read_text()), registry)
            self.assertEqual(ssh.read_bytes(), b'private ssh sentinel')
            self.assertEqual(printer.read_bytes(), b'printer sentinel')

    def test_crash_after_leaf_publish_recovers_against_staged_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            security = Path(tmp) / 'security'
            provision._tls(security, '192.0.2.2', 'owner')
            real_write = provision._atomic_write_unique

            def stop_before_initial_receipt(path, text):
                real_write(path, text)
                if path.name == 'server.crt':
                    raise OSError('simulated stop after leaf publication')

            with patch.object(provision, '_atomic_write_unique', side_effect=stop_before_initial_receipt):
                with self.assertRaisesRegex(OSError, 'leaf publication'):
                    provision.renew_tls(security, '192.0.2.2', 'c' * 32)
            published = (security / 'server.crt').read_bytes()
            self.assertTrue((security / provision.TLS_RENEWAL_JOURNAL).is_file())
            provision.renew_tls(security, '192.0.2.2', 'c' * 32)
            self.assertEqual((security / 'server.crt').read_bytes(), published)
            self.assertFalse((security / provision.TLS_RENEWAL_JOURNAL).exists())


if __name__ == '__main__':
    unittest.main()
