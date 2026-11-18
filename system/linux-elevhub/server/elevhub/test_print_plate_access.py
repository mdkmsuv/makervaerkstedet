import ipaddress
import unittest
from unittest.mock import Mock

from print_plates import allowed_peer, plate_request


class PlateAccessTests(unittest.TestCase):
    network = ipaddress.ip_network('192.0.2.0/24')

    def test_only_configured_network(self):
        self.assertTrue(allowed_peer('192.0.2.47', self.network))
        for address in ('198.51.100.2', '127.0.0.1'):
            self.assertFalse(allowed_peer(address, self.network))
        self.assertFalse(allowed_peer('192.0.2.47', None))

    def test_only_plate_fields_reach_client(self):
        printers = Mock()
        plate = dict(id='p1', name='Printer 1', status='waiting', token='a' * 32)
        printers.plates.return_value = {'plates': [dict(plate, host='private')], 'jobs': ['private']}
        self.assertEqual(plate_request(printers, None), {'plates': [plate]})

    def test_only_current_plate_can_be_requested(self):
        printers = Mock()
        printers.plates.return_value = {'ok': True}
        request = {'id': 'p1', 'token': 'a' * 32}
        self.assertEqual(plate_request(printers, request), {'ok': True})
        printers.plates.assert_called_once_with(request)
        with self.assertRaises(ValueError):
            plate_request(printers, dict(request, action='other'))
