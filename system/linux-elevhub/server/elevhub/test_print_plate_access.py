import ipaddress
import json
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest.mock import Mock
from uuid import uuid4

from print_plates import allowed_peer, handle, plate_request


class PlateAccessTests(unittest.TestCase):
    network = ipaddress.ip_network('192.0.2.0/24')

    def test_only_configured_network(self):
        self.assertTrue(allowed_peer('192.0.2.47', self.network))
        for address in ('198.51.100.2', '127.0.0.1'):
            self.assertFalse(allowed_peer(address, self.network))
        self.assertFalse(allowed_peer('192.0.2.47', None))

    def test_only_plate_fields_reach_client(self):
        printers = Mock()
        plate = dict(id='p1', name='Printer 1', status='waiting', token=uuid4().hex)
        printers.plates.return_value = {'plates': [dict(plate, host='private')], 'jobs': ['private']}
        self.assertEqual(plate_request(printers, None), {'plates': [plate]})

    def test_only_current_plate_can_be_requested(self):
        printers = Mock()
        printers.plates.return_value = {'ok': True}
        request = {'id': 'p1', 'token': uuid4().hex}
        self.assertEqual(plate_request(printers, request), {'ok': True})
        printers.plates.assert_called_once_with(request)
        with self.assertRaises(ValueError):
            plate_request(printers, dict(request, action='other'))

    def test_station_get_passes_only_a_safe_teacher_url_from_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            handler = Mock()
            handler.connection.__class__ = ssl.SSLSocket
            handler.client_address = ('192.0.2.47', 12345)
            handler.server.tilstand.mappe = Path(folder)
            handler.server.makerspace.printers.plates.return_value = {'plates': []}
            config = Path(folder) / 'pladenet.json'
            urls = ('https://print.example.test:8781/laerer', 'http://print.example.test/laerer',
                    'https://name:password@print.example.test/laerer', 'javascript:alert(1)',
                    'https://print.example.test:99999/laerer', '/laerer')
            for url in urls:
                with self.subTest(url=url):
                    config.write_text(json.dumps({'cidr': str(self.network), 'laererside': url}), encoding='utf-8')
                    self.assertTrue(handle(handler, '/api/printplader'))
                    expected = {'plates': []}
                    if url == urls[0]:
                        expected['review_url'] = url
                    handler.svar.assert_called_with(200, expected)
