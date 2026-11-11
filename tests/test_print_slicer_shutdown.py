"""Cancellation reaps a real child and cannot transfer an unfinished print."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

from app.slicer import Slicer, SliceCancelled
from app.worker import Worker
from tests import test_printer_recovery_transfer as fixture


class SlicerShutdownTests(unittest.TestCase):
    def test_cancel_reaps_running_child_promptly(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            slicer = Slicer(sys.executable, root / 'profiles', root)
            marker = root / 'pid.txt'
            result = []
            def run():
                try:
                    with (root / 'log.txt').open('wb') as log:
                        slicer._run([sys.executable, '-c',
                                     'import os,sys,time;open(sys.argv[1],"w").write(str(os.getpid()));time.sleep(60)',
                                     str(marker)], root, log)
                except BaseException as error:
                    result.append(error)
            thread = threading.Thread(target=run)
            thread.start()
            try:
                deadline = time.monotonic() + 5
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(marker.exists(), 'real child started')
                started = time.monotonic()
                slicer.cancel()
                thread.join(3)
                self.assertFalse(thread.is_alive())
                self.assertLess(time.monotonic() - started, 3)
                self.assertIsInstance(result[0], SliceCancelled)
                # Reusing a stopped slicer must never launch another child.
                with (root / 'log.txt').open('ab') as log, self.assertRaises(SliceCancelled):
                    slicer._run([sys.executable, '-c', 'raise Exception("must not run")'], root, log)
            finally:
                slicer.cancel()
                thread.join(5)

    def test_timeout_reaps_child(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            slicer = Slicer(sys.executable, root / 'profiles', root)
            with (root / 'log.txt').open('wb') as log, self.assertRaises(subprocess.TimeoutExpired):
                slicer._run([sys.executable, '-c', 'import time;time.sleep(60)'], root, log, timeout=0.1)


class WorkerCancellationTests(unittest.TestCase):
    # Reuse maintained fixture methods without collecting its reference tests.
    setUp = fixture.PrinterRecoveryTransferTests.setUp
    tearDown = fixture.PrinterRecoveryTransferTests.tearDown
    report = fixture.PrinterRecoveryTransferTests.report
    slice = fixture.PrinterRecoveryTransferTests.slice
    recovering = fixture.PrinterRecoveryTransferTests.recovering

    def test_workflow_block_explains_the_next_teacher_action(self):
        job = self.recovering(workflow=True)
        self.q.resolve(job['id'], 'isolated')
        self.report()
        self.q.printers['one']['message'] = ''
        state = self.q.snapshot()
        self.assertFalse(state['printers'][0]['can_release'])
        self.assertIn('workflowoversigt', state['printers'][0]['message'])

    def test_cancellation_preserves_authorized_workflow_and_bed_gate(self):
        job = self.q.reserve()
        from tests.test_queue_workflows import BINDING
        import json
        self.q._change(job['id'], workflow=json.dumps(BINDING), workflow_step=3,
                       workflow_version=1, workflow_authorized=1)
        self.slicer.slice.side_effect = SliceCancelled()
        self.worker.process(self.q.get_job(job['id']))
        current = self.q.get_job(job['id'])
        self.assertEqual(current['status'], 'workflow_wait')
        self.assertEqual(current['workflow_authorized'], 1)
        self.assertEqual(current['workflow'], json.dumps(BINDING))
        self.driver.upload.assert_not_called()
        self.driver.send.assert_not_called()
    def test_cancel_before_transfer_requeues_without_motion_or_transfer(self):
        job = self.q.reserve()
        self.slicer.slice.side_effect = SliceCancelled()
        self.worker.process(job)
        self.driver.upload.assert_not_called()
        self.driver.send.assert_not_called()
        self.assertEqual(self.q.get_job(job['id'])['status'], 'queued')
        self.assertEqual(self.q.get_job(job['id'])['transfer_attempts'], 0)

    def test_shutdown_after_slicing_cannot_upload(self):
        job = self.q.reserve()
        def stop_during_slice(*args):
            self.worker.stop_event.set()
            return self.slice(*args)
        self.slicer.slice.side_effect = stop_during_slice
        self.worker.process(job)
        self.driver.upload.assert_not_called()
        self.driver.send.assert_not_called()
        self.assertEqual(self.q.get_job(job['id'])['status'], 'queued')

