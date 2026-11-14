"""Local admin update-state hook only; synthetic identity/queues, no device contact."""
from contextlib import closing
import json
import sqlite3
import unittest
from unittest.mock import patch

from simple_updates import revoke_device_updates, UpdateError
from test_simple_updates import Fixture


class RevocationTests(Fixture, unittest.TestCase):
    def setUp(self):
        self.initialize()
        self.old_hash = self.registry.identitet('pc1')

    def rows(self, table):
        with closing(sqlite3.connect(self.service.path)) as db:
            return db.execute('SELECT * FROM ' + table + ' ORDER BY 1').fetchall()

    def revision(self):
        return self.rows('meta')[0][1]

    def revoke(self, identity='old'):
        revoke_device_updates(self.root, 'pc1', revoked_token_sha256=self.old_hash if identity == 'old' else identity)

    def test_offline_replay_preserves_history_other_devices_and_queue_bytes(self):
        registry = json.loads((self.root / 'registry.json').read_text())
        registry['enheder']['pc2'] = {'token_sha256': 'a' * 64, 'ui_token_sha256': 'b' * 64}
        (self.root / 'registry.json').write_text(json.dumps(registry))
        self.config['device_bindings']['pc2'] = {'role': 'student', 'platform': 'debian-amd64'}
        self.service.bind(self.binding_body()); self.service.bind(self.binding_body(device='pc2', expected_revision=1))
        self.service.order(self.order(revision=2)); self.service.order(self.order(revision=3, devices=['pc2']))
        self.service.poll('pc1', self.poll(installed=[{'component': 'studentcore', 'version': '1.0'}]))
        self.service.poll('pc2', self.poll(installed=[{'component': 'studentcore', 'version': '1.5'}]))
        histories = {t: self.rows(t) for t in ('reports', 'commands', 'highwater')}
        other_binding = [r for r in self.rows('bindings') if r[0] == 'pc2']
        other_desired = [r for r in self.rows('desired') if r[0] == 'pc2']
        files = {'printvaerksted/queue.sqlite3': b'synthetic print queue active',
                 'designvaerksted/afleveringer.sqlite3': b'synthetic laser queue full',
                 'projects/old.stl': b'old project bytes', 'projects/history.json': b'old history bytes'}
        for name, data in files.items():
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        register_bytes = (self.root / 'registry.json').read_bytes()
        revision = self.revision()
        with patch('socket.create_connection', side_effect=AssertionError('No device contact')):
            self.revoke(); self.revoke(); self.revoke(None)
        self.assertEqual(self.revision(), revision + 1)
        self.assertEqual(self.rows('revoked_devices'), [('pc1', self.old_hash)])
        for table, rows in histories.items(): self.assertEqual(self.rows(table), rows)
        self.assertEqual(self.rows('bindings'), other_binding)
        self.assertEqual(self.rows('desired'), other_desired)
        self.assertEqual((self.root / 'registry.json').read_bytes(), register_bytes)  # Caller owns register revocation.
        for name, data in files.items(): self.assertEqual((self.root / name).read_bytes(), data)
        self.assertEqual([d['device'] for d in self.reopen().overview()['devices']], ['pc2'])
        with self.assertRaisesRegex(UpdateError, 'unknown_update_device'): self.reopen().poll('pc1', self.poll())
        with self.assertRaisesRegex(UpdateError, 'unknown_update_device'): self.reopen().order(self.order(revision=self.revision()))

    def test_old_identity_cannot_rebind_during_registry_revoke_gap(self):
        self.revoke()
        with self.assertRaisesRegex(UpdateError, 'fresh_enrollment_required'):
            self.reopen().bind(self.binding_body(expected_revision=self.revision()))
        self.assertEqual(self.rows('revoked_devices'), [('pc1', self.old_hash)])

    def test_fresh_enrollment_needs_fresh_explicit_bind_and_old_orders_stay_dead(self):
        old_bind = self.binding_body(); self.service.bind(old_bind)
        old_order = self.order(revision=1); self.service.order(old_order)
        self.revoke()
        (self.root / 'registry.json').write_text(json.dumps({'enheder': {}}))
        self.assertFalse(self.registry.godkend('pc1', 'Bearer ' + self.tokens['root'], 'root'))
        self.assertFalse(self.registry.godkend('pc1', 'Bearer ' + self.tokens['ui'], 'ui'))
        registry = {'enheder': {'pc1': {'token_sha256': 'c' * 64, 'ui_token_sha256': 'd' * 64}}}
        (self.root / 'registry.json').write_text(json.dumps(registry))
        service = self.reopen()
        self.assertEqual(service.overview()['devices'], [])
        self.assertEqual(service.overview()['unbound_devices'], ['pc1'])
        with self.assertRaisesRegex(UpdateError, 'unknown_update_device'): service.order(old_order)
        service.bind(old_bind)  # Historical receipt replay must not create a new binding.
        self.assertEqual(service.overview()['devices'], [])
        service.bind(self.binding_body(expected_revision=self.revision()))
        self.assertEqual(service.poll('pc1', self.poll())['desired'], [])
        self.assertEqual(self.rows('revoked_devices'), [])
        service.order(old_order)  # Old receipt is retained; desired must not reappear.
        self.assertEqual(service.poll('pc1', self.poll())['desired'], [])

    def test_revocation_is_atomic_when_cleanup_fails(self):
        self.service.bind(self.binding_body()); self.service.order(self.order(revision=1))
        before = {t: self.rows(t) for t in ('meta', 'desired', 'bindings', 'revoked_devices')}
        with closing(sqlite3.connect(self.service.path)) as db:
            db.execute("CREATE TRIGGER synthetic_failure BEFORE DELETE ON bindings BEGIN SELECT RAISE(ABORT,'synthetic cleanup failure'); END")
            db.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'synthetic cleanup failure'): self.revoke()
        for table, rows in before.items(): self.assertEqual(self.rows(table), rows)

    def test_missing_database_retains_marker_across_first_server_start(self):
        data = self.root / 'new-hub'
        revoke_device_updates(data, 'pc1', revoked_token_sha256=self.old_hash)
        revoke_device_updates(data, 'pc1', revoked_token_sha256=self.old_hash)
        from simple_updates import SimpleUpdates
        service = SimpleUpdates(data, self.config, self.root, self.registry, verifier=self.service.verifier)
        self.assertEqual(service.overview()['revision'], 1)
        self.assertEqual(service.overview()['devices'], [])
        with self.assertRaisesRegex(UpdateError, 'fresh_enrollment_required'):
            service.bind(self.binding_body(expected_revision=1))

    def test_invalid_identity_does_not_change_existing_state(self):
        before = self.rows('meta')
        for identity in ('raw-token', 'A' * 64, 123):
            with self.subTest(identity=identity), self.assertRaisesRegex(UpdateError, 'invalid_revoked_identity'):
                self.revoke(identity)
        self.assertEqual(self.rows('meta'), before)
        self.assertEqual(self.rows('revoked_devices'), [])


if __name__ == '__main__':
    unittest.main()
