"""Complete only a verified assignment with matching actual reported inventory."""
from contextlib import closing
import sqlite3
import unittest
from test_simple_updates import Fixture


class CompletionTests(Fixture, unittest.TestCase):
    def setUp(self):
        self.initialize()

    def rows(self, table):
        with closing(sqlite3.connect(self.service.path)) as db:
            return db.execute('SELECT * FROM ' + table + ' ORDER BY 1').fetchall()

    def test_completed_matchbox_is_not_reasserted_after_native_core_upgrade(self):
        matchbox = self.release(version='1.0', sequence=1, component='matchbox')
        old_order = self.order(matchbox, component='matchbox')
        self.service.order(old_order)
        report = self.poll(installed=[{'component': 'matchbox', 'version': '1.0'}],
                           result={'revision': 1, 'state': 'succeeded', 'code': None})
        self.assertEqual(self.service.poll('pc1', report)['desired'], [])
        self.service.order(self.order(revision=1))
        commands, highwater = self.rows('commands'), self.rows('highwater')
        report = self.poll(installed=[{'component': 'matchbox', 'version': '2.0'},
                                     {'component': 'studentcore', 'version': '2.0.0'}],
                           result={'revision': 2, 'state': 'succeeded', 'code': None})
        self.assertEqual(self.service.poll('pc1', report)['desired'], [])
        self.assertEqual(self.reopen().poll('pc1', report)['desired'], [])
        self.assertEqual(self.rows('desired'), [])
        self.assertEqual(self.rows('commands'), commands)
        self.assertEqual(self.rows('highwater'), highwater)
        overview = self.service.overview()
        self.assertEqual(overview['revision'], 2)
        self.assertEqual(overview['devices'][0]['installed'], report['installed'])
        self.assertEqual(overview['devices'][0]['result'], report['result'])
        # Old request receipt replays never recreate an active operation.
        self.assertEqual(self.service.order(old_order)['revision'], 1)
        self.assertEqual(self.rows('desired'), [])
        self.assertEqual(len(self.rows('reports')), 1)

    def test_ack_alone_mismatch_wrong_revision_failed_and_missing_manifest_retain_desired(self):
        self.service.order(self.order())
        matching = [{'component': 'studentcore', 'version': '2.0.0'}]
        cases = [('ack-alone', [], {'revision': 1, 'state': 'succeeded', 'code': None}),
                 ('wrong-version', [{'component': 'studentcore', 'version': '1.0'}], {'revision': 1, 'state': 'succeeded', 'code': None}),
                 ('wrong-component', [{'component': 'matchbox', 'version': '2.0.0'}], {'revision': 1, 'state': 'succeeded', 'code': None}),
                 ('wrong-revision', matching, {'revision': 2, 'state': 'succeeded', 'code': None}),
                 ('failed', matching, {'revision': 1, 'state': 'failed', 'code': 'activation_failed'}),
                 ('inventory-only', matching, None)]
        for label, installed, result in cases:
            with self.subTest(label=label):
                self.assertEqual(len(self.service.poll('pc1', self.poll(installed=installed, result=result))['desired']), 1)
                self.assertEqual(self.service.overview()['revision'], 1)
        self.config['catalog'] = []
        self.service.poll('pc1', self.poll(installed=matching, result={'revision': 1, 'state': 'succeeded', 'code': None}))
        self.assertEqual(len(self.rows('desired')), 1)  # Not completed without a verified expected version.

    def test_result_finishes_only_its_assignment_not_other_components(self):
        matchbox = self.release(version='1.0', sequence=1, component='matchbox')
        self.service.order(self.order(matchbox, component='matchbox'))
        self.service.order(self.order(revision=1))
        report = self.poll(installed=[{'component': 'matchbox', 'version': '1.0'},
                                     {'component': 'studentcore', 'version': '2.0.0'}],
                           result={'revision': 2, 'state': 'succeeded', 'code': None})
        desired = self.service.poll('pc1', report)['desired']
        self.assertEqual([(o['component'], o['revision']) for o in desired], [('matchbox', 1)])
        self.assertEqual(self.service.overview()['revision'], 2)


if __name__ == '__main__':
    unittest.main()
