"""Regressioner for bekræftet tilstand ved udfald, genstart og defekte meldinger."""
import tempfile
import unittest
from unittest.mock import patch

from tilstand import ONLINE_SEKUNDER, Tilstand


class BekraeftelseTest(unittest.TestCase):
    def setUp(self):
        self.mappe = tempfile.TemporaryDirectory()
        self.addCleanup(self.mappe.cleanup)
        self.nu = 1000.0
        self.apps = [{'id': 'internet', 'navn': 'Internet', 'type': 'browser'}]
        self.t = Tilstand(self.mappe.name, self.apps, ur=lambda: self.nu)

    def meld(self, kilde='bjaelke', version=1, ip='192.0.2.11'):
        return self.t.status_fra_elev('vaerksted-01', ip, kilde, version=version)

    def computer(self):
        return self.t.oversigt()['computere'][0]

    def test_startside_holder_ikke_gammel_kvittering_i_live(self):
        self.meld()
        self.assertTrue(self.computer()['bekraeftet'])
        self.nu += ONLINE_SEKUNDER + 1
        self.meld('start')
        self.assertTrue(self.computer()['online'])
        self.assertFalse(self.computer()['bekraeftet'])
        self.meld()
        self.assertTrue(self.computer()['bekraeftet'])

    def test_kvittering_udloeber_selvom_browser_sender_loebende(self):
        self.meld()
        for _ in range(4):
            self.nu += 5
            self.meld('start')
        self.assertTrue(self.computer()['online'])
        self.assertFalse(self.computer()['bekraeftet'])

    def test_genstart_kraever_ny_melding_og_kvittering(self):
        self.meld()
        self.t = Tilstand(self.mappe.name, self.apps, ur=lambda: self.nu)
        self.assertFalse(self.computer()['online'])
        self.assertFalse(self.computer()['bekraeftet'])
        self.meld('start')
        self.assertFalse(self.computer()['bekraeftet'])
        self.meld()
        self.assertTrue(self.computer()['bekraeftet'])

    def test_ugyldig_kvittering_fjerner_tidligere_bekraeftelse(self):
        for version in (True, False, '1', 1.0, None, 0, -1, 2):
            with self.subTest(version=version):
                self.meld()
                self.meld(version=version)
                self.assertFalse(self.computer()['bekraeftet'])

    def test_ip_skift_arver_ikke_kvitteringen(self):
        self.meld()
        self.meld('start', ip='192.0.2.12')
        self.assertFalse(self.computer()['bekraeftet'])

    def test_fremtidig_kvittering_godkendes_ikke_senere(self):
        self.meld(version=2)
        self.t.saet({'internet': True})
        self.assertFalse(self.computer()['bekraeftet'])

    def test_ugyldig_status_opretter_ikke_en_computer(self):
        for ekstra in ({'kilde': []}, {'kilde': 'ukendt'}, {'kilde': 'start', 'visning': {}}):
            with self.subTest(ekstra=ekstra), self.assertRaises(ValueError):
                self.t.status_fra_elev('vaerksted-01', '192.0.2.11', **ekstra)
        self.assertEqual(self.t.oversigt()['computere'], [])

    def test_diskfejl_aendrer_ikke_den_aktive_politik(self):
        with patch('tilstand.skriv_json', side_effect=OSError('Disk fuld')):
            with self.assertRaises(OSError):
                self.t.saet({'internet': True})
        self.assertFalse(self.t.aaben('internet'))
        self.assertEqual(self.t.version, 1)


if __name__ == '__main__':
    unittest.main()
