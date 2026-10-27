import unittest

from app.discovery import parse_announcement


class DiscoveryTests(unittest.TestCase):
    def announcement(self, model='N1', serial='TESTPRINTER123'):
        return f'NOTIFY * HTTP/1.1\r\nUSN: {serial}\r\nDevModel.bambu.com: {model}\r\nDevName.bambu.com: Skolens printer\r\nDevConnect.bambu.com: cloud\r\nLocation: 203.0.113.40\r\n\r\n'.encode()

    def test_uses_actual_network_source_and_preserves_cloud_mode(self):
        found=parse_announcement(self.announcement(),'192.168.1.40')
        self.assertEqual(found['host'],'192.168.1.40')
        self.assertEqual(found['connection'],'cloud')
        self.assertEqual(found['name'],'Skolens printer')
        self.assertNotIn('access_code',found)

    def test_rejects_unknown_models_invalid_identifiers_and_nonlocal_sources(self):
        for packet,host in [(self.announcement(model='unknown'),'192.168.1.40'),(self.announcement(serial='../file'),'192.168.1.40'),(self.announcement(),'8.8.8.8'),(self.announcement(),'127.0.0.1'),(b'invalid','192.168.1.40')]:
            with self.subTest(packet=packet,host=host):
                self.assertIsNone(parse_announcement(packet,host))


if __name__=='__main__':unittest.main()
