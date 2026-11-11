"""Linux/WSL: rigtig CA -> server -> klient -> rollegrænse og tilbagekaldelse."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import ssl
import sys
import tempfile
import threading
import unittest
import urllib.error
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server/elevhub'))
sys.path.insert(0, str(ROOT / 'ansible/roles/kiosk/files'))

from elevhub import Hub, Server
from elevhub_klient import hent_json
from opret_serverkonfiguration import opret
from opret_sikkerhed import add_device, init_security, rotate_device
from sikkerhed import Enhedsregister, tls_kontekst
from tilstand import Tilstand

SPEC = importlib.util.spec_from_file_location('samspil_regler', ROOT / 'ansible/roles/kiosk/files/elevhub-regler.py')
REGLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REGLER)


@unittest.skipUnless(os.name == 'posix' and shutil.which('openssl'),
                     'Provisionering med Linux-filrettigheder testes i Debian WSL.')
class SikkertSamspil(unittest.TestCase):
    def test_provisionering_server_klient_og_rotation(self):
        with tempfile.TemporaryDirectory(prefix='elevhub-samspil-') as d:
            root = Path(d)
            sikkerhed = root / 'sikkerhed'
            init_security(sikkerhed, root / 'udsteder', ['localhost', '127.0.0.1'])
            add_device(sikkerhed, 'vaerksted-01')
            klientfil = sikkerhed / 'enheder/vaerksted-01.json'
            credentials = json.loads(klientfil.read_text())
            konf_sti = root / 'konfiguration.json'
            opret(ROOT / 'server/elevhub/konfiguration.json', sikkerhed, konf_sti)
            konf = json.loads(konf_sti.read_text())
            self.assertNotIn('usikker_test', konf)
            self.assertEqual(konf['elev_adresse'], '127.0.0.1')
            server = Server(('127.0.0.1', 0), Hub, tls=tls_kontekst(konf, root))
            server.navn = 'samspil'
            server.enheder = Enhedsregister(konf['enhedsregister'])
            server.tilstand = Tilstand(root / 'data', [{'id': 'internet', 'navn': 'Internet', 'type': 'browser'}])
            server.tilstand.saet({'internet': True})
            threading.Thread(target=server.serve_forever, daemon=True).start()
            klientkonf = {'hub': f'https://127.0.0.1:{server.server_address[1]}',
                          'internet_app': 'internet', 'ca_fil': str(sikkerhed / 'ca.crt'),
                          'computer': 'vaerksted-01', 'usikker_test': False}
            try:
                with patch.object(REGLER, 'KONF', klientkonf), patch.object(REGLER, 'CREDENTIALS', credentials), \
                        patch.object(REGLER, 'ANVENDT', root / 'anvendt.json'):
                    self.assertEqual(REGLER.hent_valg(), (2, True))
                    REGLER.meld_status(2)
                    self.assertTrue(server.tilstand.oversigt()['computere'][0]['bekraeftet'])
                    ui = dict(token=credentials['ui_token'], computer='vaerksted-01',
                              ca_fil=klientkonf['ca_fil'])
                    resultat = hent_json(klientkonf['hub'], '/api/status', **ui,
                                         data={'computer': 'vaerksted-01', 'visning': 'internet'})
                    self.assertFalse(resultat['gaa_hjem'])
                    with self.assertRaises(urllib.error.HTTPError) as fejl:
                        hent_json(klientkonf['hub'], '/api/enhed/politik', **ui)
                    self.assertEqual(fejl.exception.code, 401)
                    # De rigtige klientfunktioner reagerer på rotation uden servergenstart.
                    REGLER.meld_anvendt(2, True)
                    rotate_device(sikkerhed, 'vaerksted-01', confirm=True)
                    with self.assertRaises(urllib.error.HTTPError) as fejl:
                        REGLER.hent_valg()
                    self.assertEqual(fejl.exception.code, 401)
                    self.assertFalse(REGLER.ANVENDT.exists())
                    credentials.update(json.loads(klientfil.read_text()))
                    self.assertEqual(REGLER.hent_valg(), (2, True))
            finally:
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    unittest.main()
