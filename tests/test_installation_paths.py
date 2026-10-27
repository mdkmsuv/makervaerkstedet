import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.paths import installation


class InstallationPathsTests(unittest.TestCase):
    def test_selected_program_uses_stable_settings_and_preserves_inert_legacy_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            program, runtime, data = root / 'program', root / 'runtime', root / 'data'
            for folder in (program, runtime, data):
                folder.mkdir()
            (runtime / 'installation.json').write_text(json.dumps({'ai_enabled': True, 'port': 18765}), encoding='utf-8')
            (program / 'elevhub-package.json').write_text(json.dumps({'localAiIncluded': False}), encoding='utf-8')
            with patch('app.generation_runtime.selected_data_root', return_value=data), patch(
                    'app.generation_runtime.active_details', return_value={'runtime_root': str(runtime)}):
                self.assertEqual(installation(program), {'ai_enabled': True, 'port': 18765, 'data_root': str(data)})

    def test_legacy_settings_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'installation.json').write_text(json.dumps({'ai_enabled': False, 'port': 18765}), encoding='utf-8')
            self.assertEqual(installation(root), {'ai_enabled': False, 'port': 18765})


if __name__ == '__main__':
    unittest.main()
