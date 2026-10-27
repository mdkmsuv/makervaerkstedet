"""Behavioral enrollment tests use isolated files and loopback TLS only."""
import base64
import concurrent.futures
import importlib.util
import json
from pathlib import Path
import secrets
import ssl
import struct
import sys
import tempfile
import threading
import http.client
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


enrollment = module('school_enrollment', HERE / 'enrollment.py')
onboarding = module('school_onboarding', ROOT / 'linux-elevhub/installation/onboarding.py')
provision = module('school_provision', ROOT / 'windows-elevhub/server/provision.py')


def key():
    return 'ssh-ed25519 ' + base64.b64encode(struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32) + secrets.token_bytes(32)).decode('ascii')


class EnrollmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ca_temp = tempfile.TemporaryDirectory()
        cls.tls = Path(cls.ca_temp.name) / 'security'
        # Temporary test material is deliberately isolated from production
        # ACL setup.  Do not weaken the provisioner itself for this fixture.
        with patch.object(provision, '_restrict_private_path', return_value=None):
            provision._tls(cls.tls, '192.0.2.10')

    @classmethod
    def tearDownClass(cls):
        cls.ca_temp.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'security').mkdir()
        (self.root / 'admin-ssh').mkdir()
        (self.root / 'hub').mkdir()
        for path in self.tls.iterdir():
            (self.root / 'security' / path.name).write_bytes(path.read_bytes())
        self.school = {'schemaVersion': 1, 'school_name': 'Testskolen', 'server': '192.0.2.10',
                       'role': 'vm', 'theme': 'yellow', 'student_subnet': '192.0.2.0/24', 'admin_public_key': key()}
        enrollment.write_json(self.root / 'school.json', self.school)
        self.request = {'computer': 'rum-a-01', 'installation_id': secrets.token_hex(32), 'ssh_host_key': key()}

    def issue(self):
        self.request['code'] = enrollment.issue_ticket(self.root, self.request['computer'], now=1000)
        return self.request

    def test_public_connection_has_no_credentials(self):
        data = enrollment.connection(self.root)
        self.assertNotIn('token', json.dumps(data))
        self.assertNotIn('PRIVATE KEY', json.dumps(data))
        path = self.root / 'school-connection.json'
        enrollment.write_json(path, data)
        self.assertEqual(onboarding.load_connection(path)['theme'], 'yellow')

    def test_ticket_registers_tokens_and_retry_only_exact_installation(self):
        request = self.issue()
        result = enrollment.redeem(self.root, request, '192.0.2.20', now=1001)
        repeat = enrollment.redeem(self.root, request, '192.0.2.21', now=1002)
        self.assertEqual(result, repeat)
        state = enrollment.read_json(self.root / 'security/enheder.json')['enheder']['rum-a-01']
        self.assertEqual(set(state), {'token_sha256', 'ui_token_sha256'})
        self.assertEqual(enrollment.read_json(self.root / 'security/ssh-pins.json')['rum-a-01'], request['ssh_host_key'])
        self.assertNotIn(result['device']['token'], json.dumps(state))
        for change in ({'installation_id': secrets.token_hex(32)}, {'ssh_host_key': key()}):
            with self.assertRaises(ValueError):
                enrollment.redeem(self.root, dict(request, **change), '192.0.2.20', now=1002)

    def test_wrong_expired_name_and_wrong_network_never_register(self):
        request = self.issue()
        cases = [(dict(request, code='AAAA-AAAA-AAAA'), '192.0.2.20', 1001),
                 (dict(request, computer='anden-pc'), '192.0.2.20', 1001),
                 (request, '192.0.3.20', 1001), (request, '192.0.2.20', 3000)]
        for value, address, now in cases:
            with self.assertRaises(ValueError):
                enrollment.redeem(self.root, value, address, now=now)
        self.assertEqual(enrollment.read_json(self.root / 'security/enheder.json'), {'enheder': {}})

    def test_new_ticket_revokes_old_and_existing_name_rejected(self):
        first = dict(self.issue())
        second = enrollment.issue_ticket(self.root, 'rum-a-01', now=1001)
        with self.assertRaises(ValueError):
            enrollment.redeem(self.root, first, '192.0.2.20', now=1002)
        enrollment.redeem(self.root, dict(first, code=second), '192.0.2.20', now=1002)
        with self.assertRaises(ValueError):
            enrollment.issue_ticket(self.root, 'rum-a-01', now=1003)

    def test_two_clients_cannot_claim_one_ticket(self):
        first = dict(self.issue())
        second = dict(first, installation_id=secrets.token_hex(32))
        def attempt(value):
            try:
                enrollment.redeem(self.root, value, '192.0.2.20', now=1002)
                return True
            except ValueError:
                return False
        with concurrent.futures.ThreadPoolExecutor(2) as executor:
            results = list(executor.map(attempt, [first, second]))
        self.assertEqual(sorted(results), [False, True])

    def test_revocation_invalidates_old_ticket_and_allows_new_identity(self):
        request = dict(self.issue())
        enrollment.redeem(self.root, request, '192.0.2.20', now=1001)
        enrollment.revoke_device(self.root, 'rum-a-01')
        with self.assertRaises(ValueError):
            enrollment.redeem(self.root, request, '192.0.2.20', now=1002)
        request.update(code=enrollment.issue_ticket(self.root, 'rum-a-01', now=1002),
                       ssh_host_key=key(), installation_id=secrets.token_hex(32))
        enrollment.redeem(self.root, request, '192.0.2.20', now=1003)
        self.assertEqual(enrollment.read_json(self.root / 'security/ssh-pins.json')['rum-a-01'], request['ssh_host_key'])

    def test_revocation_journal_recovers_after_middle_file_failure_and_retry(self):
        request = dict(self.issue())
        enrollment.redeem(self.root, request, '192.0.2.20', now=1001)
        enrollment.write_json(self.root / 'security/enheder.json',
                              {'enheder': {'rum-a-01': enrollment.read_json(self.root / 'security/enheder.json')['enheder']['rum-a-01'],
                                           'rum-b-01': {'token_sha256': 'b', 'ui_token_sha256': 'b'}}})
        enrollment.write_json(self.root / 'security/ssh-pins.json',
                              {'rum-a-01': request['ssh_host_key'], 'rum-b-01': key()})
        state = enrollment.read_json(self.root / 'security/enrollment.json')
        state['tickets']['other'] = {'computer': 'rum-b-01', 'expires': 9999}
        enrollment.write_json(self.root / 'security/enrollment.json', state)
        original = enrollment.write_json
        failed = {'value': False}

        def fail_once(path, value):
            if Path(path).name == 'enheder.json' and not failed['value']:
                failed['value'] = True
                raise OSError('simuleret diskfejl')
            return original(path, value)

        with patch.object(enrollment, 'write_json', side_effect=fail_once):
            with self.assertRaises(RuntimeError):
                enrollment.revoke_device(self.root, 'rum-a-01')
        journal = self.root / enrollment.REVOKE_JOURNAL
        self.assertTrue(journal.exists())
        # A fresh process performs recovery under the same lock.
        restarted = module('school_enrollment_restart', HERE / 'enrollment.py')
        restarted.revoke_device(self.root, 'rum-a-01')
        self.assertFalse(journal.exists())
        self.assertNotIn('rum-a-01', enrollment.read_json(self.root / 'security/enheder.json')['enheder'])
        self.assertNotIn('rum-a-01', enrollment.read_json(self.root / 'security/ssh-pins.json'))
        self.assertIn('rum-b-01', enrollment.read_json(self.root / 'security/enheder.json')['enheder'])
        self.assertIn('rum-b-01', enrollment.read_json(self.root / 'security/ssh-pins.json'))
        self.assertFalse(any(ticket['computer'] == 'rum-a-01'
                             for ticket in enrollment.read_json(self.root / 'security/enrollment.json')['tickets'].values()))

    def test_revoke_recovery_failure_after_each_target_write_is_retryable(self):
        request = dict(self.issue())
        enrollment.redeem(self.root, request, '192.0.2.20', now=1001)
        original = enrollment.write_json
        for target in ('enrollment.json', 'enheder.json', 'ssh-pins.json'):
            # Restore a registered device for each independent interrupted run.
            enrollment.write_json(self.root / 'security/enheder.json',
                                  {'enheder': {'rum-a-01': {'token_sha256': 'a', 'ui_token_sha256': 'a'}}})
            enrollment.write_json(self.root / 'security/ssh-pins.json', {'rum-a-01': request['ssh_host_key']})
            failed = {'value': False}
            def fail_once(path, value, target=target):
                if Path(path).name == target and not failed['value']:
                    failed['value'] = True
                    raise OSError('simuleret diskfejl')
                return original(path, value)
            with patch.object(enrollment, 'write_json', side_effect=fail_once):
                with self.assertRaises(RuntimeError):
                    enrollment.revoke_device(self.root, 'rum-a-01')
            enrollment.revoke_device(self.root, 'rum-a-01')
            self.assertFalse((self.root / enrollment.REVOKE_JOURNAL).exists())

    def test_journal_unlink_failure_keeps_recovery_marker_for_retry(self):
        request = dict(self.issue())
        enrollment.redeem(self.root, request, '192.0.2.20', now=1001)
        original_unlink = Path.unlink
        failed = {'value': False}
        def fail_journal_unlink(path, *args, **kwargs):
            if path == self.root / enrollment.REVOKE_JOURNAL and not failed['value']:
                failed['value'] = True
                raise OSError('simuleret unlink-fejl')
            return original_unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', fail_journal_unlink):
            with self.assertRaises(RuntimeError):
                enrollment.revoke_device(self.root, 'rum-a-01')
        self.assertTrue((self.root / enrollment.REVOKE_JOURNAL).exists())
        enrollment.revoke_device(self.root, 'rum-a-01')
        self.assertFalse((self.root / enrollment.REVOKE_JOURNAL).exists())

    def test_next_ordinary_enrollment_operation_recovers_pending_revoke(self):
        request = dict(self.issue())
        enrollment.redeem(self.root, request, '192.0.2.20', now=1001)
        original = enrollment.write_json
        failed = {'value': False}
        def fail_once(path, value):
            if Path(path).name == 'enheder.json' and not failed['value']:
                failed['value'] = True
                raise OSError('simuleret diskfejl')
            return original(path, value)
        with patch.object(enrollment, 'write_json', side_effect=fail_once):
            with self.assertRaises(RuntimeError):
                enrollment.revoke_device(self.root, 'rum-a-01')
        code = enrollment.issue_ticket(self.root, 'rum-b-01', now=1002)
        self.assertTrue(code)
        self.assertNotIn('rum-a-01', enrollment.read_json(self.root / 'security/enheder.json')['enheder'])
        self.assertFalse((self.root / enrollment.REVOKE_JOURNAL).exists())

    def test_invalid_journal_is_rejected_without_mutating_state(self):
        paths = [self.root / 'security/enrollment.json', self.root / 'security/enheder.json',
                 self.root / 'security/ssh-pins.json']
        enrollment.write_json(paths[0], {'tickets': {}})
        enrollment.write_json(paths[1], {'enheder': {}})
        enrollment.write_json(paths[2], {})
        (self.root / 'security/enrollment').mkdir(exist_ok=True)
        before = [path.read_bytes() for path in paths]
        enrollment.write_json(self.root / enrollment.REVOKE_JOURNAL,
                              {'schemaVersion': True, 'operation': 'revoke-device',
                               'computer': 'rum-a-01', 'tampered': True})
        with self.assertRaises(RuntimeError):
            enrollment.issue_ticket(self.root, 'rum-b-01', now=1000)
        self.assertEqual([path.read_bytes() for path in paths], before)
        self.assertTrue((self.root / enrollment.REVOKE_JOURNAL).exists())

    def test_oversize_journal_is_rejected_before_recovery(self):
        huge = 'x' * 700_000
        enrollment.write_json(self.root / 'security/enrollment.json',
                              {'tickets': {'other': {'computer': 'rum-b-01', 'expires': 999, 'note': huge}}})
        enrollment.write_json(self.root / 'security/enheder.json', {'enheder': {'rum-a-01': {}, 'rum-b-01': {'note': huge}}})
        enrollment.write_json(self.root / 'security/ssh-pins.json', {'rum-a-01': 'key', 'rum-b-01': huge})
        journal = self.root / enrollment.REVOKE_JOURNAL
        journal.parent.mkdir(exist_ok=True)
        journal.write_text('x' * (enrollment.MAX_REVOKE_INTENT_BYTES + 1), encoding='utf-8')
        with self.assertRaises(RuntimeError):
            enrollment.issue_ticket(self.root, 'rum-b-01', now=1000)
        self.assertTrue(journal.exists())

    def test_recovery_preserves_identity_added_after_interrupted_revoke(self):
        request = dict(self.issue())
        enrollment.redeem(self.root, request, '192.0.2.20', now=1001)
        original = enrollment.write_json
        failed = {'value': False}
        def fail_once(path, value):
            if Path(path).name == 'enheder.json' and not failed['value']:
                failed['value'] = True
                raise OSError('simuleret diskfejl')
            return original(path, value)
        with patch.object(enrollment, 'write_json', side_effect=fail_once):
            with self.assertRaises(RuntimeError):
                enrollment.revoke_device(self.root, 'rum-a-01')
        registry = enrollment.read_json(self.root / 'security/enheder.json')
        registry['enheder']['rum-b-01'] = {'token_sha256': 'b', 'ui_token_sha256': 'b'}
        enrollment.write_json(self.root / 'security/enheder.json', registry)
        pins = enrollment.read_json(self.root / 'security/ssh-pins.json')
        pins['rum-b-01'] = key()
        enrollment.write_json(self.root / 'security/ssh-pins.json', pins)
        state = enrollment.read_json(self.root / 'security/enrollment.json')
        state['tickets']['other'] = {'computer': 'rum-b-01', 'expires': 9999}
        enrollment.write_json(self.root / 'security/enrollment.json', state)
        enrollment.revoke_device(self.root, 'rum-a-01')
        self.assertIn('rum-b-01', enrollment.read_json(self.root / 'security/enheder.json')['enheder'])
        self.assertIn('rum-b-01', enrollment.read_json(self.root / 'security/ssh-pins.json'))
        self.assertIn('other', enrollment.read_json(self.root / 'security/enrollment.json')['tickets'])

    def test_profile_reuses_firstboot_contract_and_keeps_local_identity(self):
        result = enrollment.redeem(self.root, self.issue(), '192.0.2.20', now=1001)
        target = self.root / 'config'
        onboarding.save_profile(result, target, ROOT / 'linux-elevhub/installation/firstboot.py')
        self.assertEqual(json.loads((target / 'settings.json').read_text())['admin_sources'], ['192.0.2.10'])
        self.assertNotIn('PRIVATE KEY', (target / 'admin.pub').read_text())

    def test_ssh_uses_registration_pin_and_subnet(self):
        enrollment.redeem(self.root, self.issue(), '192.0.2.20', now=1001)
        status = {'rum-a-01': {'ip': '192.0.2.20', 'sidst_set': 1001, 'ssh_host_key': self.request['ssh_host_key']}}
        enrollment.write_json(self.root / 'hub/computere.json', status)
        command = enrollment.ssh_command(self.root, 'rum-a-01', now=1002)
        self.assertIn('StrictHostKeyChecking=yes', command)
        self.assertIn('HostKeyAlias=rum-a-01', command)
        self.assertEqual(command[-1], 'elevhub@192.0.2.20')
        for field, value in [('ssh_host_key', key()), ('ip', '192.0.3.20'), ('sidst_set', 1)]:
            changed = {'rum-a-01': dict(status['rum-a-01'], **{field: value})}
            enrollment.write_json(self.root / 'hub/computere.json', changed)
            with self.assertRaises(ValueError):
                enrollment.ssh_command(self.root, 'rum-a-01', now=1002)

    def test_client_tls_is_verified_and_redirect_not_followed(self):
        connection = enrollment.connection(self.root)
        import http.client
        with patch.object(http.client, 'HTTPSConnection') as transport:
            response = transport.return_value.getresponse.return_value
            response.status = 302
            response.read.return_value = b'{}'
            with self.assertRaises(ValueError):
                onboarding.enroll(connection, 'rum-a-01', 'AAAA-BBBB-CCCC', secrets.token_hex(32), key())
            context = transport.call_args.kwargs['context']
            self.assertTrue(context.check_hostname)
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            self.assertEqual(transport.return_value.request.call_count, 1)

    def test_real_https_enrollment_and_untrusted_ca(self):
        """Real loopback sockets, real CA verification, real handler and registry."""
        cfg = dict(self.school, server='127.0.0.1', student_subnet='127.0.0.0/8')
        class LoopbackServer(enrollment.EnrollmentServer):
            def server_bind(inner):
                inner.server_address = ('127.0.0.1', 0)
                return super().server_bind()
        with patch.object(enrollment, 'school', return_value=cfg):
            request = dict(self.request, code=enrollment.issue_ticket(self.root, self.request['computer']))
            server = LoopbackServer(self.root)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                ca = (self.root / 'security/ca.crt').read_text()
                context = ssl.create_default_context(cadata=ca)
                transport = http.client.HTTPSConnection('127.0.0.1', server.server_port, context=context, timeout=5)
                transport.request('POST', '/enroll', json.dumps(request))
                response = transport.getresponse()
                self.assertEqual(response.status, 200)
                profile = json.loads(response.read())
                transport.close()
                self.assertEqual(profile['device']['computer'], 'rum-a-01')
                untrusted = http.client.HTTPSConnection('127.0.0.1', server.server_port,
                                                        context=ssl.create_default_context(), timeout=5)
                try:
                    with self.assertRaises(ssl.SSLCertVerificationError):
                        untrusted.request('POST', '/enroll', json.dumps(request))
                finally:
                    untrusted.close()
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=5)

    def test_broken_key_and_subnet_fail_before_any_ticket(self):
        for value in ('ssh-ed25519 AAAA', 'ssh-rsa AAAA', 'ssh-ed25519 ' + 'A' * 68):
            with self.assertRaises(ValueError):
                enrollment.host_key(value)
        self.school['student_subnet'] = '0.0.0.0/0'
        enrollment.write_json(self.root / 'school.json', self.school)
        with self.assertRaises(ValueError):
            self.issue()


if __name__ == '__main__':
    unittest.main()
