"""An interrupted TLS operation must be settled before another task operator."""
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import unittest

from app import school_tls, school_maintenance


class TLSMaintenanceExclusionTests(unittest.TestCase):
    def test_interrupted_renewal_blocks_another_operator_before_task_capture(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime, data = root / 'runtime', root / 'data'
            (runtime / 'control').mkdir(parents=True)
            (runtime / 'program').mkdir()
            (data / 'security').mkdir(parents=True)
            (data / 'konfiguration.json').write_text(json.dumps({'elev_adresse': '192.0.2.10'}))
            record = {'format': 1, 'installation_id': 'a' * 32, 'revision': 1,
                      'runtime_root': str(runtime), 'program_root': str(runtime / 'program'),
                      'data_root': str(data), 'manifest_sha256': 'b' * 64}
            calls = []
            class Controller:
                def capture(self, *args):
                    calls.append('capture')
                    return {}
                def stop(self, *args):
                    calls.append('stop')
            def interrupted(*args):
                raise RuntimeError('fixture interruption')
            with self.assertRaisesRegex(RuntimeError, 'fixture interruption'):
                school_tls.execute(runtime_root=runtime, controller=Controller(),
                    selector=lambda _: record, renewer=interrupted, locker=lambda _: nullcontext())
            journal = runtime / 'control' / school_tls.JOURNAL
            before = journal.read_bytes()
            with self.assertRaisesRegex(RuntimeError, 'certifikatfornyelsen'):
                school_maintenance.execute('update', runtime_root=runtime,
                    folder=root / 'new-operation', materialized_folder=root / 'new-copy',
                    program_root=runtime / 'program', controller=Controller())
            self.assertEqual(calls, ['capture', 'stop'])
            self.assertEqual(journal.read_bytes(), before)
            self.assertFalse((root / 'new-operation').exists())


if __name__ == '__main__':
    unittest.main()
