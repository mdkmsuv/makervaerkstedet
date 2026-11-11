import base64
import http.client
from io import BytesIO
import json
from pathlib import Path
import tempfile
import threading
import unittest

from PIL import Image

from app.laser_station import Station, create_station_server


SVG = ('<svg xmlns="http://www.w3.org/2000/svg" data-laservaerksted="1" '
       'width="60mm" height="30mm" viewBox="0 0 60 30">'
       '<g data-operation="cut"><path data-operation="cut" d="M0 0 H60 V30 H0 Z"/></g></svg>')


class FakeQueue:
    base_url = 'http://fake-queue.invalid'
    logged_in = True

    def __init__(self):
        self.svg = SVG

    def jobs(self, *, summary=False):
        return [{'id': 'a' * 32, 'student': 'Ada', 'classroom': '4.A', 'project': 'Skilt',
                 'status': 'pending', 'payload_available': True}]

    def prepared_svg(self, job_id):
        if job_id != 'a' * 32:
            raise ValueError('Unknown job')
        return self.svg


def photograph():
    image = Image.new('RGB', (120, 80), (200, 150, 100))
    buffer = BytesIO()
    image.save(buffer, 'PNG')
    return {'image': 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii'),
            'corners': [[0, 0], [120, 0], [120, 80], [0, 80]], 'heightMm': 3, 'confirmed': True}


def rectangle(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


class StationMaterialWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.queue = FakeQueue()
        self.opened = []
        self.station = Station(self.queue, Path(self.temp.name) / 'plates', ghost=None,
                               opener=self.opened.append)
        self.job_id = 'a' * 32
        self.item = {'id': self.job_id, 'x': 10, 'y': 10, 'rotation': 0}

    def request(self, scene=None, item=None, **extra):
        request = {'material': 'wood_3mm', 'items': [item or self.item], 'open': False}
        if scene:
            request.update(sceneId=scene['id'], sceneRevision=scene['revision'])
        request.update(extra)
        return request

    def image_and_regions(self, *, hole=None, outline=None):
        scene = self.station.material_image(photograph())['scene']
        region = {'id': 'sheet', 'points': outline or rectangle(0, 0, 300, 200),
                  'holes': [hole] if hole else []}
        scene = self.station.material_regions({'sceneId': scene['id'], 'revision': scene['revision'],
                                               'regions': [region]})['scene']
        return scene

    def approve_regions(self, scene):
        return self.station.material_approve({'sceneId': scene['id'], 'revision': scene['revision'],
                                              'confirmed': True})['scene']

    def test_photo_exports_without_region_or_placement_approval(self):
        # A photograph alone, without drawn regions, never blocks the plate.
        scene = self.station.material_image(photograph())['scene']
        self.assertTrue(Path(self.station.open_plate(self.request(scene))['file']).is_file())
        scene = self.image_and_regions()
        opened = self.station.open_plate(self.request(scene))
        self.assertTrue(Path(opened['file']).is_file())
        self.assertEqual(self.opened, [])
        record = json.loads(Path(opened['file']).with_suffix('.json').read_text(encoding='utf-8'))
        self.assertNotIn('teacherApproved', record)
        self.assertEqual(record['materialScene']['id'], scene['id'])
        self.assertNotIn('data:image', Path(opened['file']).read_text(encoding='utf-8'))
        self.assertNotIn('<image', Path(opened['file']).read_text(encoding='utf-8'))

    def test_moved_rotated_or_changed_plate_exports_without_new_approval(self):
        payload = self.request(self.image_and_regions())
        for changed in (dict(payload, items=[dict(self.item, x=20)]),
                        dict(payload, items=[dict(self.item, rotation=90)]),
                        dict(payload, material='acrylic_3mm')):
            with self.subTest(changed=changed):
                self.assertTrue(Path(self.station.open_plate(changed)['file']).is_file())
        self.queue.svg = SVG.replace('H60 V30', 'H59 V30')
        self.assertTrue(Path(self.station.open_plate(payload)['file']).is_file())

    def test_stale_scene_and_revision_rejected(self):
        scene = self.approve_regions(self.image_and_regions())
        for changed in (dict(self.request(scene), sceneId='0' * 32),
                        dict(self.request(scene), sceneRevision=scene['revision'] - 1),
                        dict(self.request(scene), sceneRevision=True)):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.station.approve_plate(dict(changed, confirmed=True))
        self.station.material_image(photograph())
        with self.assertRaises(ValueError):
            self.station.open_plate(self.request(scene))

    def test_hole_and_edge_margin_are_warnings(self):
        for regions in ({'hole': rectangle(20, 20, 90, 70)}, {'outline': rectangle(0, 0, 70, 40)}):
            with self.subTest(regions=regions):
                scene = self.image_and_regions(**regions)
                result = self.station.check(self.request(scene), run_flux=False)[0]
                self.assertEqual(result['problems'], [])
                self.assertTrue(result['warnings'])
                self.assertTrue(Path(self.station.open_plate(self.request(scene))['file']).is_file())

    def test_overlap_is_a_warning_and_outside_the_bed_is_a_problem(self):
        from app import laser_plate
        geometry = {'width': 60, 'height': 30}
        entries = [{'name': name, 'geometry': geometry, 'x': 10, 'y': 10, 'rotation': 0} for name in ('A', 'B')]
        warnings = []
        self.assertEqual(laser_plate.check_plate(entries, overlaps=warnings), [])
        self.assertEqual(len(warnings), 1)
        entries[1]['x'] = 590
        self.assertEqual(len(laser_plate.check_plate(entries, overlaps=[])), 1)

    def test_blank_manual_mode_still_exports_without_photo_token(self):
        opened = self.station.open_plate(self.request())
        self.assertTrue(Path(opened['file']).is_file())
        record = json.loads(Path(opened['file']).with_suffix('.json').read_text(encoding='utf-8'))
        self.assertNotIn('materialScene', record)
        self.assertNotIn('teacherApproved', record)

    def test_material_actions_require_teacher_session(self):
        self.queue.logged_in = False
        from app.laser_station import NotLoggedIn
        with self.assertRaises(NotLoggedIn):
            self.station.material_image(photograph())
        with self.assertRaises(NotLoggedIn):
            self.station.material_regions({'sceneId': '0' * 32, 'revision': 1, 'regions': []})

    def test_photo_http_route_requires_login_and_same_origin(self):
        server = create_station_server(self.station, 0)
        server.RequestHandlerClass.log_message = lambda *args: None
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_port
        body = json.dumps(photograph())

        def send(origin, route='/api/station/material/image'):
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
            connection.request('POST', route, body,
                               {'Host': f'127.0.0.1:{port}', 'Origin': origin,
                                'Content-Type': 'application/json'})
            response = connection.getresponse()
            status = response.status
            response.read()
            connection.close()
            return status

        self.queue.logged_in = False
        self.assertEqual(send(f'http://127.0.0.1:{port}'), 401)
        self.queue.logged_in = True
        self.assertEqual(send('http://outside.invalid'), 403)
        self.assertEqual(send(f'http://127.0.0.1:{port}'), 200)
        self.assertEqual(send(f'http://127.0.0.1:{port}', '/api/station/material/analyze'), 404)
        self.assertNotIn('vision', self.station.state())
        self.assertFalse(hasattr(self.station, 'material_analyze'))


if __name__ == '__main__':
    unittest.main()
