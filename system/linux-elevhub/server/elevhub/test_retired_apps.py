"""Retiring a launcher preserves the original config and saved projects."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

import apps
import elevhub
import regler


class RetiredAppsTest(unittest.TestCase):
    def configuration(self):
        config = json.loads(Path(__file__).with_name('konfiguration.json').read_text(encoding='utf-8'))
        config['apps'].extend([
            {'id': 'word', 'navn': 'Word', 'type': 'native', 'native_app': 'word'},
            {'id': 'cadoodle', 'navn': 'CaDoodle', 'type': 'native', 'native_app': 'cadoodle'},
        ])
        return config

    def load(self, config):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            path.write_text(json.dumps(config), encoding='utf-8')
            return elevhub.laes_konfiguration(path)

    def test_legacy_configuration_keeps_word_and_print_contract(self):
        config = self.configuration()
        expected = [a for a in config['apps'] if a['id'] != 'cadoodle']
        loaded = self.load(config)
        self.assertEqual(loaded['apps'], expected)
        printing = next(a for a in loaded['apps'] if a['id'] == '3dprint')
        self.assertEqual((printing['navn'], printing['port'], printing['bagved'], printing['regler']),
                         ('3D-print', 8770, '127.0.0.1:8765', 'printvaerksted'))

    def test_retired_legacy_ids_and_native_aliases_are_filtered(self):
        config = self.configuration()
        for app_id in ('skolegpt', 'kredsloeb', 'datatraener'):
            config['apps'].append({'id': app_id, 'navn': app_id, 'standard_aaben': True})
            config['apps'].append({'id': 'alias-' + app_id, 'navn': app_id, 'native_app': app_id})
        loaded = self.load(config)
        self.assertEqual({a['id'] for a in loaded['apps']},
                         {'3dprint', 'laser', 'folie', 'microbit', 'scratch', 'internet', 'word'})
        self.assertTrue(all(a['standard_aaben'] for a in loaded['apps'] if a['id'] not in ('internet', 'word')))
        self.assertFalse(next(a for a in loaded['apps'] if a['id'] == 'internet')['standard_aaben'])
        for app_id in apps.RETIRED_APPS:
            with self.subTest(app_id=app_id):
                candidate = self.configuration()
                candidate['apps'] = [{'id': app_id, 'navn': app_id}]
                with self.assertRaises(ValueError):
                    apps.valider(candidate, regler.REGLER)

    def test_loading_preserves_configuration_and_project_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            project = Path(folder) / 'saved.cadoodle'
            original = json.dumps(self.configuration(), ensure_ascii=False, indent=2).encode('utf-8')
            project_bytes = b'\x00saved project\xff\n'
            path.write_bytes(original)
            project.write_bytes(project_bytes)
            elevhub.laes_konfiguration(path)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(project.read_bytes(), project_bytes)

    def test_native_validation_accepts_word_rejects_retired_and_unknown(self):
        for app_id in ('word', 'cadoodle', 'unknown'):
            with self.subTest(app_id=app_id):
                config = {'hub_port': 8773, 'laerer_port': 8780, 'apps': [
                    {'id': app_id, 'navn': app_id, 'type': 'native', 'native_app': app_id}]}
                if app_id == 'word':
                    self.assertIs(apps.valider(config, regler.REGLER), config)
                else:
                    with self.assertRaises(ValueError):
                        apps.valider(config, regler.REGLER)

    def test_migration_is_repeatable_and_other_validation_stays_active(self):
        first = self.load(self.configuration())
        self.assertEqual(self.load(first), first)
        collision = copy.deepcopy(self.configuration())
        collision['apps'][0]['port'] = collision['hub_port']
        with self.assertRaises(ValueError):
            self.load(collision)
        malformed = {'hub_port': 8773, 'laerer_port': 8780, 'apps': [None]}
        with self.assertRaises(ValueError):
            self.load(malformed)


if __name__ == '__main__':
    unittest.main()
