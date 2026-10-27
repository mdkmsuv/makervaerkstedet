"""Hele kæden: lokal designtjeneste -> serverens elevindgang -> laserindbakken.

test_laser_local.py bruger laser_server direkte som server. Her går
afleveringen gennem den rigtige Indgang med regler.laser og lærerens
åbn/luk-tilstand, præcis som på skolen.
"""
import json
import sys
import tempfile
import threading
import unittest
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ELEVHUB = ROOT / 'linux-elevhub' / 'server' / 'elevhub'
if str(ELEVHUB) not in sys.path:
    sys.path.insert(0, str(ELEVHUB))

from app.laser_local import create_local_servers  # noqa: E402
from app.laser_server import create_server  # noqa: E402
from elevhub import Indgang, Server, adresse  # noqa: E402
from regler import REGLER  # noqa: E402
from tilstand import Tilstand  # noqa: E402

SVG = ('<svg xmlns="http://www.w3.org/2000/svg" data-laservaerksted="1" width="100mm" height="60mm" '
       'viewBox="0 0 100 60"><g data-operation="cut" fill="none" stroke="#000">'
       '<path d="M10 10H60V40H10Z" data-operation="cut"/></g></svg>')


def laser_app():
    konf = json.loads((ELEVHUB / 'konfiguration.json').read_text(encoding='utf-8'))
    return next(app for app in konf['apps'] if app['id'] == 'laser')


class GennemIndgangenTest(unittest.TestCase):
    def setUp(self):
        self.mappe = tempfile.TemporaryDirectory()
        self.addCleanup(self.mappe.cleanup)
        # Serverens laserindbakke (den rigtige Inbox) bag indgangen.
        self.central = create_server(Path(self.mappe.name) / 'laser', '127.0.0.1', 0)
        self._start(self.central)

        app = laser_app()
        self.tilstand = Tilstand(str(Path(self.mappe.name) / 'hub'), [app])
        self.indgang = Server(('127.0.0.1', 0), Indgang)
        self.indgang.navn, self.indgang.app, self.indgang.tilstand = app['id'], app, self.tilstand
        self.indgang.regel = REGLER[app['regler']]
        self.indgang.bagved = adresse(f'127.0.0.1:{self.central.server_address[1]}')
        self._start(self.indgang)

        self.laser, self.folie = create_local_servers(
            f'http://127.0.0.1:{self.indgang.server_address[1]}', laser_port=0, folie_port=0)
        self._start(self.laser)
        self._start(self.folie)

    def _start(self, server):
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

    def aflever(self, **ekstra):
        data = {'elevnavn': 'Alma', 'klasse': '5A', 'projekt': 'Nøglering',
                'requestId': str(uuid.uuid4()), 'svg': SVG, **ekstra}
        port = self.laser.port
        # Samme Origin som browseren sender fra den lokale side.
        req = urllib.request.Request(
            f'http://127.0.0.1:{port}/api/laser/submit',
            data=json.dumps(data).encode('utf-8'), method='POST',
            headers={'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{port}'})
        try:
            with urllib.request.urlopen(req, timeout=20) as svar:
                return svar.status, json.loads(svar.read())
        except urllib.error.HTTPError as fejl:
            with fejl:
                return fejl.code, json.loads(fejl.read())

    def test_aflevering_naar_laererens_koe_gennem_indgangen(self):
        status, svar = self.aflever()
        self.assertEqual(status, 200, svar)
        self.assertFalse(svar['duplicate'])
        jobs = self.central.inbox.jobs()
        self.assertEqual([job['id'] for job in jobs], [svar['id']])
        self.assertEqual(jobs[0]['student'], 'Alma')

    def test_laererens_lukning_afviser_afleveringen(self):
        self.tilstand.saet({'laser': False})
        status, svar = self.aflever()
        self.assertEqual(status, 400)
        self.assertEqual(svar['error'], 'Læreren har lukket Laser lige nu. Gå tilbage til start.')
        self.assertEqual(self.central.inbox.jobs(), [])

    def test_genaabning_tillader_aflevering_igen(self):
        self.tilstand.saet({'laser': False})
        self.assertEqual(self.aflever()[0], 400)
        self.tilstand.saet({'laser': True})
        status, svar = self.aflever()
        self.assertEqual(status, 200, svar)

    def test_serverens_validering_naar_frem_til_eleven(self):
        status, svar = self.aflever(elevnavn='')
        self.assertEqual(status, 400)
        self.assertIn('elevnavn', svar['error'])


if __name__ == '__main__':
    unittest.main()
