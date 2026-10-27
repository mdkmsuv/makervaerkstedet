"""Regler for tidsbegrænset lukning af værkstedsapps."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tilstand import Tilstand


APPS = [
    {'id': 'laser', 'navn': 'Laser', 'type': 'vaerksted', 'standard_aaben': True},
    # Manglende type skal følge den bagudkompatible værkstedsstandard.
    {'id': 'folie', 'navn': 'Folie', 'standard_aaben': True},
    {'id': 'internet', 'navn': 'Internet', 'type': 'browser', 'standard_aaben': False},
]


class Ur:
    def __init__(self, vaerdi=1_700_000_000):
        self.vaerdi = float(vaerdi)

    def __call__(self):
        return self.vaerdi

    def frem(self, sekunder):
        self.vaerdi += sekunder


class LukningTest(unittest.TestCase):
    def setUp(self):
        self.mappe = tempfile.TemporaryDirectory()
        self.addCleanup(self.mappe.cleanup)
        self.ur = Ur()
        self.t = Tilstand(self.mappe.name, APPS, ur=self.ur)

    def app(self, svar, app_id):
        return next(a for a in svar['apps'] if a['id'] == app_id)

    def gemt(self):
        return json.loads((Path(self.mappe.name) / 'tilstand.json').read_text(encoding='utf-8'))

    def test_lukning_giver_to_timer_og_deadline_i_disk_og_svar(self):
        version = self.t.version
        self.assertEqual(self.t.saet({'laser': False}), version + 1)
        deadline = self.ur.vaerdi + 7200

        self.assertEqual(self.t.lukket_indtil['laser'], deadline)
        self.assertEqual(self.gemt()['lukket_indtil']['laser'], deadline)
        self.assertEqual(self.app(self.t.apps_til_elever(), 'laser')['lukket_indtil'], deadline)
        self.assertEqual(self.app(self.t.oversigt(), 'laser')['lukket_indtil'], deadline)

    def test_vaerksteder_far_uafhaengige_deadlines(self):
        self.t.saet({'laser': False})
        laser_deadline = self.t.lukket_indtil['laser']
        self.ur.frem(100)
        self.t.saet({'folie': False})

        self.assertEqual(self.t.lukket_indtil['laser'], laser_deadline)
        self.assertEqual(self.t.lukket_indtil['folie'], self.ur.vaerdi + 7200)
        self.assertNotIn('internet', self.t.lukket_indtil)
        self.ur.frem(7100)
        self.assertTrue(self.t.aaben('laser'))
        self.assertFalse(self.t.aaben('folie'))

    def test_gentaget_lukning_forlaenger_ikke_men_aaben_og_ny_lukning_goer(self):
        self.t.saet({'laser': False})
        gammel_deadline = self.t.lukket_indtil['laser']
        gammel_version = self.t.version
        self.ur.frem(100)

        self.assertEqual(self.t.saet({'laser': False}), gammel_version)
        self.assertEqual(self.t.lukket_indtil['laser'], gammel_deadline)

        self.t.saet({'laser': True})
        self.assertTrue(self.t.aaben('laser'))
        self.assertNotIn('laser', self.t.lukket_indtil)
        self.t.saet({'laser': False})
        self.assertEqual(self.t.lukket_indtil['laser'], self.ur.vaerdi + 7200)

    def test_udloeb_er_lukket_ved_7199_og_aabner_ved_7200_med_en_versionsaendring(self):
        self.t.saet({'laser': False, 'folie': False})
        lukket_version = self.t.version
        self.ur.frem(7199)
        foer = self.t.apps_til_elever()
        self.assertEqual(foer['version'], lukket_version)
        self.assertFalse(self.app(foer, 'laser')['aaben'])
        self.assertFalse(self.app(foer, 'folie')['aaben'])

        self.ur.frem(1)
        efter = self.t.apps_til_elever()
        self.assertEqual(efter['version'], lukket_version + 1)
        self.assertTrue(self.app(efter, 'laser')['aaben'])
        self.assertTrue(self.app(efter, 'folie')['aaben'])
        self.assertIsNone(self.app(efter, 'laser')['lukket_indtil'])
        self.assertEqual(self.t.oversigt()['version'], lukket_version + 1)

    def test_internet_lukkes_uden_deadline_og_aabner_ikke_automatisk(self):
        self.assertNotIn('internet', self.t.lukket_indtil)
        self.assertIsNone(self.app(self.t.apps_til_elever(), 'internet')['lukket_indtil'])
        self.t.saet({'internet': True})
        self.t.saet({'internet': False})
        version = self.t.version
        self.assertNotIn('internet', self.t.lukket_indtil)

        self.ur.frem(7200)
        svar = self.t.apps_til_elever()
        self.assertEqual(svar['version'], version)
        self.assertFalse(self.t.aaben('internet'))
        self.assertNotIn('internet', self.t.lukket_indtil)

    def test_udloeb_fjerner_gammel_lukning_uden_at_sende_elev_hjem(self):
        self.t.status_fra_elev('elev-01', '10.0.0.11', 'bjaelke', visning='laser', version=self.t.version)
        self.t.saet({'laser': False})
        self.ur.frem(7200)

        status = self.t.status_fra_elev('elev-01', '10.0.0.11', 'bjaelke', visning='laser', version=self.t.version)
        visning = self.t.visning_fra_elev('elev-01', 'laser')
        self.assertFalse(status['gaa_hjem'])
        self.assertFalse(visning['gaa_hjem'])
        self.assertTrue(self.t.aaben('laser'))

    def test_udloeb_fjerner_allerede_ventende_gaa_hjem_besked(self):
        self.t.status_fra_elev('elev-03', '10.0.0.13', 'bjaelke', visning='laser', version=self.t.version)
        self.t.saet({'laser': False})
        gammel = self.t.status_fra_elev('elev-03', '10.0.0.13', 'bjaelke', version=self.t.version)
        self.assertTrue(gammel['gaa_hjem'])
        self.assertEqual(self.t.computere['elev-03'].get('gaa_hjem_venter'), 'laser')

        self.ur.frem(7200)
        self.t.apps_til_elever()
        ui = self.t.visning_fra_elev('elev-03')
        self.assertFalse(ui['gaa_hjem'])
        self.assertNotIn('gaa_hjem_venter', self.t.computere['elev-03'])

    def test_gammel_kvittering_er_ugyldig_efter_versionsaendring(self):
        self.t.saet({'laser': False})
        kvitteret_version = self.t.version
        self.ur.frem(7199)
        self.t.status_fra_elev('elev-02', '10.0.0.12', 'bjaelke', version=kvitteret_version)
        self.assertTrue(self.t.oversigt()['computere'][0]['bekraeftet'])

        self.ur.frem(1)
        self.assertFalse(self.t.oversigt()['computere'][0]['bekraeftet'])
        self.assertEqual(self.t.version, kvitteret_version + 1)

    def test_lukket_editor_forbliver_lukket_gennem_poll_genaabning_og_genstart(self):
        self.t.status_fra_elev('elev-01', '10.0.0.11', 'bjaelke', visning='laser')
        self.t.saet({'laser': False})
        self.t.status_fra_elev('elev-01', '10.0.0.11', 'bjaelke')
        for _ in range(3):
            self.assertEqual(self.t.visning_fra_elev('elev-01')['lukket_app'], 'laser')
        self.t.saet({'laser': True})
        self.assertIsNone(self.t.visning_fra_elev('elev-01')['lukket_app'])
        self.t.saet({'laser': False})
        self.assertEqual(self.t.visning_fra_elev('elev-01')['lukket_app'], 'laser')
        self.ur.frem(31)
        self.t.status_fra_elev('elev-01', '10.0.0.11', 'bjaelke')
        restarted = Tilstand(self.mappe.name, APPS, ur=self.ur)
        self.assertEqual(restarted.visning_fra_elev('elev-01')['lukket_app'], 'laser')
        self.ur.frem(7200)
        self.assertIsNone(restarted.visning_fra_elev('elev-01')['lukket_app'])

    def test_ui_uden_root_heartbeat_faar_lukning_men_opretter_ikke_enhed(self):
        self.t.saet({'laser': False})
        self.assertEqual(self.t.visning_fra_elev('elev-01', 'laser')['lukket_app'], 'laser')
        self.assertEqual(self.t.computere, {})

    def test_genstart_bevarer_fremtidig_deadline_og_aabner_passeret_deadline(self):
        self.t.saet({'laser': False})
        deadline = self.t.lukket_indtil['laser']
        version = self.t.version

        igen = Tilstand(self.mappe.name, APPS, ur=self.ur)
        self.assertFalse(igen.aaben('laser'))
        self.assertEqual(igen.lukket_indtil['laser'], deadline)
        self.assertEqual(igen.version, version)

        self.ur.frem(7200)
        boot = Tilstand(self.mappe.name, APPS, ur=self.ur)
        self.assertTrue(boot.aaben('laser'))
        self.assertNotIn('laser', boot.lukket_indtil)
        self.assertEqual(boot.version, version + 1)

    def test_gammel_lukning_uden_deadline_migreres_en_gang(self):
        sti = Path(self.mappe.name) / 'tilstand.json'
        sti.write_text(json.dumps({'aabne': {'laser': False, 'folie': True, 'internet': False}, 'version': 9}),
                       encoding='utf-8')

        boot = Tilstand(self.mappe.name, APPS, ur=self.ur)
        deadline = boot.lukket_indtil['laser']
        self.assertEqual(deadline, self.ur.vaerdi + 7200)
        self.assertEqual(self.gemt()['lukket_indtil']['laser'], deadline)

        self.ur.frem(60)
        igen = Tilstand(self.mappe.name, APPS, ur=self.ur)
        self.assertEqual(igen.lukket_indtil['laser'], deadline)
        self.assertEqual(self.gemt()['lukket_indtil']['laser'], deadline)

    def test_diskfejl_ved_deadlineoprettelse_bevarer_aktiv_tilstand(self):
        filfoer = (Path(self.mappe.name) / 'tilstand.json').read_text(encoding='utf-8')
        version = self.t.version
        with patch('tilstand.skriv_json', side_effect=OSError('Disk fuld')):
            with self.assertRaises(OSError):
                self.t.saet({'laser': False})

        self.assertTrue(self.t.aaben('laser'))
        self.assertEqual(self.t.version, version)
        self.assertNotIn('laser', self.t.lukket_indtil)
        self.assertEqual((Path(self.mappe.name) / 'tilstand.json').read_text(encoding='utf-8'), filfoer)

    def test_diskfejl_ved_automatisk_aabning_bevarer_lukket_tilstand(self):
        self.t.saet({'laser': False})
        version = self.t.version
        deadline = self.t.lukket_indtil['laser']
        self.ur.frem(7200)

        with patch('tilstand.skriv_json', side_effect=OSError('Disk fuld')):
            with self.assertRaises(OSError):
                self.t.apps_til_elever()

        self.assertFalse(self.t.aabne['laser'])
        self.assertEqual(self.t.version, version)
        self.assertEqual(self.t.lukket_indtil['laser'], deadline)
        self.assertTrue(self.app(self.t.apps_til_elever(), 'laser')['aaben'])


if __name__ == '__main__':
    unittest.main()
