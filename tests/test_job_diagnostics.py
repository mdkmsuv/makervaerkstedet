import subprocess
import tempfile
import unittest
from pathlib import Path
from app.job_diagnostics import save_diagnostic, read_diagnostic
from app.queue_storage import cleanup_job_artifacts


class DiagnosticsTests(unittest.TestCase):
    def test_timeout_and_log_survive_terminal_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            job = 'a' * 32
            root = Path(folder)
            (root/'uploads').mkdir()
            (root/'uploads'/f'{job}.stl').write_bytes(b'model')
            sliced = root/'sliced'/job
            sliced.mkdir(parents=True)
            (sliced/'slicer.log').write_text('slicer stopped at layer 10', encoding='utf-8')
            save_diagnostic(root, job, 'slicing', subprocess.TimeoutExpired(['private-command'], 600))
            cleanup_job_artifacts(root, job)
            detail = read_diagnostic(root, job)
            self.assertIn('10 minutter', detail['message'])
            self.assertIn('layer 10', detail['log_tail'])
            self.assertNotIn('private-command', str(detail))

    def test_transfer_exception_credentials_not_saved(self):
        with tempfile.TemporaryDirectory() as folder:
            save_diagnostic(folder, 'b'*32, 'transfer', OSError('password=secret'))
            self.assertNotIn('secret', str(read_diagnostic(folder, 'b'*32)))

    def test_invalid_identifier_rejected(self):
        with self.assertRaises(ValueError):
            read_diagnostic('.', '../settings')


if __name__ == '__main__':
    unittest.main()
