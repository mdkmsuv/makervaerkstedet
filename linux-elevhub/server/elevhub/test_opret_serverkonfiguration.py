import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from opret_serverkonfiguration import opret


class OpretServerkonfigurationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.basis = self.root / 'basis.json'
        source = Path(__file__).with_name('konfiguration.json')
        self.basis.write_text(source.read_text(encoding='utf-8'), encoding='utf-8')
        self.security = self.root / 'security'
        self.security.mkdir()
        for name in ('server.crt', 'server.key', 'enheder.json'):
            (self.security / name).write_text('{}', encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def _opret(self, **kwargs):
        output = self.root / 'output.json'
        with mock.patch('opret_serverkonfiguration.tls_kontekst'), mock.patch(
                'opret_serverkonfiguration.Enhedsregister'):
            opret(self.basis, self.security, output, **kwargs)
        return json.loads(output.read_text(encoding='utf-8'))

    def test_printerreference_bruger_loopback_port_og_absolute_key_path(self):
        data_dir = self.root / 'printer-data'
        result = self._opret(printer_port=8765, printer_data_dir=data_dir)
        self.assertEqual(result['makerspace_printers']['backend'], '127.0.0.1:8765')
        self.assertEqual(result['makerspace_printers']['key_file'],
                         str(data_dir.resolve() / 'makerspace-bridge.key'))
        self.assertFalse((data_dir / 'makerspace-bridge.key').exists())
        self.assertNotIn('mkbridge_', json.dumps(result))

    def test_uden_tilvalg_bevarer_eksisterende_printeropsætning(self):
        basis = json.loads(self.basis.read_text(encoding='utf-8'))
        basis['makerspace_printers'] = {
            'backend': '127.0.0.1:8765', 'key_file': 'printer/makerspace-bridge.key'}
        self.basis.write_text(json.dumps(basis), encoding='utf-8')
        result = self._opret()
        self.assertEqual(result['makerspace_printers'], basis['makerspace_printers'])

    def test_tilvalg_skal_komme_sammen_og_port_er_streng(self):
        for kwargs in ({'printer_port': 8765}, {'printer_data_dir': self.root / 'data'}):
            with self.assertRaisesRegex(ValueError, 'skal angives sammen'):
                self._opret(**kwargs)
        for port in (0, 65536, True, '8765'):
            with self.assertRaisesRegex(ValueError, 'mellem 1 og 65535'):
                self._opret(printer_port=port, printer_data_dir=self.root / 'data')


if __name__ == '__main__':
    unittest.main()
