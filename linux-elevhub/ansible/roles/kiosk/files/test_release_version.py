import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location('elevhub_regler_release_test', HERE / 'elevhub-regler.py')
regler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(regler)


class ReleaseVersionTests(unittest.TestCase):
    def version(self, value):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'release.json'
            if value is not None:
                path.write_text(value, encoding='utf-8')
            return regler.software_version(path)

    def test_valid_release_id_is_returned(self):
        self.assertEqual(self.version(json.dumps({'release_id': '2026.10.11'})), '2026.10.11')

    def test_missing_or_malformed_release_is_unknown(self):
        self.assertIsNone(self.version(None))
        self.assertIsNone(self.version('{'))
        self.assertIsNone(self.version(json.dumps({'expected_client_release': '2026.10.11'})))
        self.assertIsNone(self.version(json.dumps({'release_id': '2026/10/11'})))
        self.assertIsNone(self.version(json.dumps({'release_id': 'x' * 65})))

    def test_authenticated_root_status_carries_software_version(self):
        regler.KONF = {'usikker_test': False, 'ca_fil': '/tmp/test-ca.pem',
                       'hub': 'https://hub.test', 'computer': 'pc1'}
        regler.CREDENTIALS = {'computer': 'pc1', 'token': 'secret'}
        svar = {'version': 3, 'apps': [], 'gaa_hjem': False}
        with mock.patch.object(regler, 'software_version', return_value='2026.10.11'), \
                mock.patch.object(regler, 'ssh_vaertsnoegle', return_value=None), \
                mock.patch.object(regler, 'hent_json', return_value=svar) as request:
            self.assertIs(regler.meld_status(3), svar)
        self.assertEqual(request.call_args.kwargs['data']['software_version'], '2026.10.11')
        self.assertEqual(request.call_args.args[1], '/api/enhed/status')

    def test_unknown_software_version_is_omitted_from_root_status(self):
        regler.KONF = {'usikker_test': False, 'ca_fil': '/tmp/test-ca.pem',
                       'hub': 'https://hub.test', 'computer': 'pc1'}
        regler.CREDENTIALS = {'computer': 'pc1', 'token': 'secret'}
        svar = {'version': 3, 'apps': [], 'gaa_hjem': False}
        with mock.patch.object(regler, 'software_version', return_value=None), \
                mock.patch.object(regler, 'ssh_vaertsnoegle', return_value=None), \
                mock.patch.object(regler, 'hent_json', return_value=svar) as request:
            regler.meld_status(3)
        self.assertNotIn('software_version', request.call_args.kwargs['data'])


if __name__ == '__main__':
    unittest.main()
