import json
import tempfile
import unittest
from pathlib import Path

from makerspace_store import MakerspaceStore
from makerspace_workflows import templates


ACTOR = {'id': 'teacher:' + 'a' * 32, 'role': 'teacher',
         'scopes': ['makerspace:read', 'machines:write', 'workflows:write', 'jobs:write', 'audit:read']}


class External:
    def __init__(self):
        self.calls = []

    def fingerprint(self, envelope):
        return json.dumps(envelope, sort_keys=True, separators=(',', ':'))

    def execute(self, actor, envelope):
        self.calls.append(envelope)
        if envelope['action'] == 'printer.workflow.assign':
            return {'ok': True, 'action': envelope['action'], 'result': {
                'id': 'legacy-printer', 'name': 'P1', 'location': 'Lab', 'model': 'P1S',
                'host': '10.0.0.5', 'serial': 'SERIAL', 'enabled': False,
                'bed_type': 'Textured PEI Plate', 'version': 2}}
        return {'ok': True, 'action': envelope['action'], 'result': {
            'id': 'job-' + '1' * 8, 'printer_id': 'legacy-printer', 'filename': 'design.3mf',
            'status': 'active', 'version': 2, 'workflow_id': 'b' * 32,
            'workflow_revision': 1, 'workflow_name': 'Print',
            'step': {'id': 'approve', 'label': 'Godkend', 'role': 'teacher', 'action': 'approve'},
            'production_authorized': False, 'secret': 'drop-me'}}


class PrinterWorkflowStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = MakerspaceStore(Path(self.tmp.name) / 'hub.sqlite3')
        self.external = External()
        created = self.store.execute(ACTOR, {'action': 'workflow.create',
            'params': {'definition': templates()['3d_print']}, 'expected_version': 0,
            'request_id': '1' * 32})
        self.workflow_id = created['result']['id']
        self.store.execute(ACTOR, {'action': 'workflow.publish', 'params': {'id': self.workflow_id},
                                   'expected_version': 1, 'request_id': '2' * 32})

    def tearDown(self):
        self.tmp.cleanup()

    def command(self, request='3' * 32, workflow_id=None, revision=1):
        return {'action': 'printer.workflow.assign', 'params': {'id': 'legacy-printer',
                'workflow_id': workflow_id or self.workflow_id, 'revision': revision},
                'expected_version': 1, 'request_id': request}

    def test_resolves_published_definition_and_retries_original_request(self):
        command = self.command()
        first = self.store.execute(ACTOR, command, external=self.external)
        self.assertEqual(len(self.external.calls), 1)
        backend = self.external.calls[0]
        self.assertEqual(backend['params']['id'], 'legacy-printer')
        self.assertEqual(backend['params']['workflow']['revision'], 1)
        self.assertEqual(backend['params']['workflow']['definition']['kind'], '3d_print')
        self.assertNotEqual(backend['params'], command['params'])
        self.assertEqual(self.store.execute(ACTOR, command, external=self.external), first)
        self.assertEqual(len(self.external.calls), 1)

    def test_unassign_requires_both_nullable_fields_and_bad_revision_is_rejected(self):
        unassign = {'action': 'printer.workflow.assign', 'params': {'id': 'legacy-printer',
                     'workflow_id': None, 'revision': None}, 'expected_version': 1,
                     'request_id': '4' * 32}
        self.store.execute(ACTOR, unassign, external=self.external)
        self.assertIsNone(self.external.calls[-1]['params']['workflow'])
        with self.assertRaises(ValueError):
            self.store.execute(ACTOR, self.command('5' * 32, self.workflow_id, 99), external=self.external)

    def test_job_advance_returns_strict_public_job(self):
        command = {'action': 'printer.job.advance', 'params': {'id': 'job-' + '1' * 8,
            'step_id': 'approve', 'confirmed': True}, 'expected_version': 1, 'request_id': '6' * 32}
        result = self.store.execute(ACTOR, command, external=self.external)
        self.assertNotIn('secret', result['result'])
        self.assertEqual(set(result['result']), {'id', 'printer_id', 'filename', 'status', 'version',
                         'workflow_id', 'workflow_revision', 'workflow_name', 'step', 'production_authorized'})


if __name__ == '__main__':
    unittest.main()
