"""Interrupted rollback keeps its own source fresh and its history authenticated."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from app import generation_activation as activation
from app import generation_materialization as materialization
from app import generation_rollback as rollback
from app import generation_transaction as preparation
from app import installation_update as update
from tests import test_generation_rollback as fixture


@unittest.skipUnless(sys.platform == 'win32' and fixture.fixture.fixture.install_fixture.ARCHIVE.is_file(),
                     'requires the cached Windows embedded runtime')
class GenerationRollbackRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fx = fixture.GenerationRollbackTests()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.work = self.fx.data / 'new-work-before-rollback.txt'
        self.work.write_text('Current generation work', encoding='utf-8')
        self.before = self.fx.fingerprint()
        self.parent_plan = preparation._json(self.fx.completed / activation.PLAN)
        self.parent_receipt = activation._receipt_path(self.fx.runtime, self.parent_plan['operation_id'])
        self.receipt_bytes = self.parent_receipt.read_bytes()

    def corrupt_receipt(self):
        value = json.loads(self.receipt_bytes)
        value['plan_sha256'] = '0' * 64
        self.parent_receipt.write_bytes(activation._bytes(value))

    def test_interrupted_prepare_rechecks_history_and_preserves_newer_work_on_cancel(self):
        with patch.object(update, '_prepare_locked', side_effect=RuntimeError('interrupted backup')):
            with self.assertRaisesRegex(RuntimeError, 'interrupted backup'):
                self.fx.prepare()
        journal_path = self.fx.destination / preparation.JOURNAL
        journal = preparation._json(journal_path)
        self.assertEqual(journal['rollback_of']['operation_id'], self.parent_plan['operation_id'])
        self.assertNotEqual(journal['operation_id'], self.parent_plan['operation_id'])
        self.assertEqual(self.before, self.fx.fingerprint())
        self.corrupt_receipt()
        with self.assertRaisesRegex(ValueError, 'kvittering'):
            preparation.resume(self.fx.runtime, self.fx.destination)
        self.parent_receipt.write_bytes(self.receipt_bytes)
        original = journal_path.read_bytes()
        journal['rollback_of']['plan_sha256'] = '1' * 64
        journal_path.write_bytes(activation._bytes(journal))
        with self.assertRaisesRegex(ValueError, 'anden operation'):
            preparation.resume(self.fx.runtime, self.fx.destination)
        journal_path.write_bytes(original)
        report = preparation.resume(self.fx.runtime, self.fx.destination)
        self.assertEqual(report['state'], 'prepared')
        self.assertEqual(report['rollback_of'], self.parent_plan['operation_id'])
        self.work.write_text('Even newer work after interrupted preparation', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Kildedata er ændret'):
            preparation.resume(self.fx.runtime, self.fx.destination)
        # Cancellation only reopens its still-selected source; it does not need
        # a valid old program archive or an intact older receipt to do so.
        self.corrupt_receipt()
        result = preparation.cancel(self.fx.runtime, self.fx.destination)
        self.assertEqual(result['state'], 'cancelled')
        self.assertEqual(self.fx.current, self.fx.fx.selected())
        self.assertEqual(self.work.read_text(encoding='utf-8'), 'Even newer work after interrupted preparation')

    def test_interrupted_rollback_publication_can_abort_even_if_parent_history_is_unavailable(self):
        result = self.fx.prepare()
        materialization.materialize(self.fx.destination / preparation.PREPARED, self.fx.final,
                                    expected_operation_id=result['operation_id'])
        save = activation._save_state

        def fail(folder, plan, phase, recovery_selection=None):
            if phase == 'releasing':
                raise RuntimeError('interrupted rollback publication')
            return save(folder, plan, phase, recovery_selection)

        with patch.object(activation, '_save_state', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'interrupted rollback publication'):
                activation.activate(self.fx.runtime, self.fx.destination, self.fx.final, host=self.fx.fx.host)
        self.assertEqual(self.fx.fx.selected()['manifest_sha256'], self.fx.original_manifest)
        self.corrupt_receipt()
        with self.assertRaisesRegex(ValueError, 'kvittering'):
            activation.activate(self.fx.runtime, self.fx.destination, host=self.fx.fx.host)
        result = activation.abort_unpublished(self.fx.runtime, self.fx.destination, host=self.fx.fx.host)
        expected = dict(self.fx.current, revision=self.fx.current['revision'] + 2)
        self.assertEqual(self.fx.fx.selected(), expected)
        self.assertEqual(result['state'], 'aborted')
        self.assertEqual(self.before, self.fx.fingerprint(expected))
        self.assertEqual(activation.abort_unpublished(self.fx.runtime, self.fx.destination, host=self.fx.fx.host), result)
        self.assertFalse(self.fx.fx.barrier.exists())
        self.assertTrue((self.fx.final / '.restore-hold.json').exists())
        self.assertEqual(self.fx.fx.fx.inspect().returncode, 0)
        # The old activation receipt proves revision 2, not the compensating
        # revision 4. A fresh retry requires the explicit, closed abort receipt.
        self.parent_receipt.write_bytes(self.receipt_bytes)
        retry_folder = self.fx.destination.with_name('rollback-retry')
        with self.assertRaisesRegex(ValueError, 'aktive version'):
            rollback.prepare(self.fx.runtime, self.fx.completed, retry_folder)
        self.assertFalse(retry_folder.exists())
        self.assertFalse(self.fx.fx.barrier.exists())
        self.work.write_text('New work after the closed abort', encoding='utf-8')
        latest = self.fx.fingerprint(expected)
        retry = rollback.prepare(self.fx.runtime, self.fx.completed, retry_folder,
                                 settled_folder=self.fx.destination)
        self.assertEqual(retry['state'], 'prepared')
        retry_journal = preparation._json(retry_folder / preparation.JOURNAL)
        self.assertEqual(retry_journal['rollback_of']['settled']['operation_id'], result['operation_id'])
        self.assertNotEqual(retry['operation_id'], result['operation_id'])
        candidate = retry_folder / preparation.PREPARED / update.CANDIDATE / 'roots/server'
        self.assertEqual((candidate / self.work.name).read_text(encoding='utf-8'),
                         'New work after the closed abort')
        self.assertEqual(retry, preparation.resume(self.fx.runtime, retry_folder))
        settled_receipt = activation._receipt_path(self.fx.runtime, result['operation_id'])
        original = settled_receipt.read_bytes()
        changed = json.loads(original)
        changed['plan_sha256'] = '2' * 64
        settled_receipt.write_bytes(activation._bytes(changed))
        try:
            with self.assertRaisesRegex(ValueError, 'kvittering'):
                preparation.resume(self.fx.runtime, retry_folder)
            # Even with a damaged historic receipt this fresh attempt can be
            # cancelled, keeping the selected pair and all work after abort.
            self.assertEqual(preparation.cancel(self.fx.runtime, retry_folder)['state'], 'cancelled')
        finally:
            settled_receipt.write_bytes(original)
        self.assertEqual(self.fx.fx.selected(), expected)
        self.assertEqual(self.fx.fingerprint(expected), latest)
        self.assertFalse(self.fx.fx.barrier.exists())


if __name__ == '__main__':
    unittest.main()
