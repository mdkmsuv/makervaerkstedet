import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
import uuid
import xml.etree.ElementTree as ET

from app import laser_plate as plate
from app.laser_ghost import FluxGhost
from app.laser_server import create_server
from app.laser_station import QueueClient, Station, create_station_server

NS = '{http://www.w3.org/2000/svg}'
CODE = 'test-only-station-code'


def delivery(paths, width=120, height=80):
    groups = ''.join(f'<g data-operation="{op}">' + ''.join(f'<path data-operation="{op}" d="{d}"/>' for d in ds) + '</g>'
                     for op, ds in paths)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" data-laservaerksted="1" width="{width}mm" height="{height}mm" '
            f'viewBox="0 0 {width} {height}">{groups}</svg>')


KEYRING = delivery([('engrave-line', ['M30 20 L60 20']), ('cut', ['M20 10 H80 V40 H20 Z', 'M25 25 a5 5 0 1 0 10 0 a5 5 0 1 0 -10 0 Z'])])
CURVES = delivery([('cut', ['M10 10 C10 -10 50 -10 50 10 S90 30 90 10 Q100 0 110 10 T110 30 Z'])])


def prepared(svg):
    from app.laser_server import clean_svg
    from app.laser_images import prepared_svg
    return prepared_svg(clean_svg(svg)[0], {})


class PlateGeometryTests(unittest.TestCase):
    def test_bbox_of_lines_arcs_and_curves(self):
        g = plate.job_geometry(prepared(KEYRING))
        self.assertEqual(g['bbox'], [20.0, 10.0, 80.0, 40.0])
        self.assertEqual(g['operations'], ['engrave-line', 'cut'])
        curves = plate.job_geometry(prepared(CURVES))
        x0, y0, x1, y1 = curves['bbox']
        self.assertAlmostEqual(x0, 10, places=3)
        self.assertLess(y0, 0)  # the first curve bulges above y=0
        self.assertAlmostEqual(y0, -5, delta=0.05)
        self.assertGreater(x1, 110)

    def test_compact_arc_flags_are_parsed(self):
        points = plate.path_points('M0 0 a10 10 0 0110 10')
        self.assertAlmostEqual(points[-1][0], 10)
        self.assertAlmostEqual(points[-1][1], 10)

    def test_placement_matrix_puts_the_box_at_the_requested_corner(self):
        bbox = [20.0, 10.0, 80.0, 40.0]
        for rotation in (0, 90, 180, 270):
            m = plate.placement_matrix(bbox, 100, 50, rotation)
            corners = [plate.apply_matrix(m, p) for p in ((20, 10), (80, 10), (20, 40), (80, 40))]
            w, h = plate.rotated_size(60, 30, rotation)
            self.assertAlmostEqual(min(c[0] for c in corners), 100)
            self.assertAlmostEqual(min(c[1] for c in corners), 50)
            self.assertAlmostEqual(max(c[0] for c in corners), 100 + w)
            self.assertAlmostEqual(max(c[1] for c in corners), 50 + h)

    def test_auto_place_keeps_gap_margin_and_bed(self):
        items = [{'id': f'j{i}', 'width': 90 + i * 7, 'height': 40 + (i % 3) * 25} for i in range(14)]
        placements, unplaced = plate.auto_place(items)
        self.assertEqual(unplaced, [])
        boxes = []
        for p in placements:
            item = next(i for i in items if i['id'] == p['id'])
            w, h = plate.rotated_size(item['width'], item['height'], p['rotation'])
            box = (p['x'], p['y'], p['x'] + w, p['y'] + h)
            self.assertGreaterEqual(box[0], 5 - 1e-6)
            self.assertGreaterEqual(box[1], 5 - 1e-6)
            self.assertLessEqual(box[2], plate.BED_WIDTH - 5 + 1e-6)
            self.assertLessEqual(box[3], plate.BED_HEIGHT - 5 + 1e-6)
            for other in boxes:
                separated = box[2] + 4 <= other[0] + 1e-6 or other[2] + 4 <= box[0] + 1e-6 or \
                    box[3] + 4 <= other[1] + 1e-6 or other[3] + 4 <= box[1] + 1e-6
                self.assertTrue(separated, f'{box} is too close to {other}')
            boxes.append(box)

    def test_auto_place_reports_what_does_not_fit(self):
        placements, unplaced = plate.auto_place([{'id': 'big', 'width': 700, 'height': 700}, {'id': 'small', 'width': 10, 'height': 10}])
        self.assertEqual(unplaced, ['big'])
        self.assertEqual([p['id'] for p in placements], ['small'])

    def test_check_plate_finds_overlap_and_outside(self):
        g = plate.job_geometry(prepared(KEYRING))
        entries = [dict(id='a', name='A', geometry=g, x=10, y=10, rotation=0),
                   dict(id='b', name='B', geometry=g, x=40, y=20, rotation=0),
                   dict(id='c', name='C', geometry=g, x=570, y=10, rotation=0)]
        problems = plate.check_plate(entries)
        self.assertIn('B overlapper A.', problems)
        self.assertIn('C ligger uden for laserens område.', problems)

    def test_read_placements_rejects_unknown_and_bad_values(self):
        with self.assertRaises(ValueError):
            plate.read_placements({'items': [{'id': 'x', 'x': 1, 'y': 1, 'rotation': 0}]}, {'a'})
        with self.assertRaises(ValueError):
            plate.read_placements({'items': [{'id': 'a', 'x': 1, 'y': 1, 'rotation': 45}]}, {'a'})
        with self.assertRaises(ValueError):
            plate.read_placements({'items': [{'id': 'a', 'x': True, 'y': 1, 'rotation': 0}]}, {'a'})
        with self.assertRaises(ValueError):
            plate.read_placements({'items': [{'id': 'a', 'x': 1, 'y': 1, 'rotation': 0}] * 2}, {'a'})


class PresetAndDocumentTests(unittest.TestCase):
    def setUp(self):
        self.presets = plate.load_presets()

    def test_beambox_ii_presets_come_from_beam_studio(self):
        self.assertEqual(self.presets['model'], 'fbb2')
        self.assertIn('Beam Studio', self.presets['source'])
        wood = plate.material_by_id(self.presets, 'wood_3mm')
        self.assertEqual(wood['cutPreset']['key'], 'wood_3mm_cutting')
        self.assertEqual(wood['engravePreset']['key'], 'wood_engraving')
        slate = plate.material_by_id(self.presets, 'slate')
        self.assertIsNone(slate['cut'])
        with self.assertRaises(ValueError):
            plate.material_by_id(self.presets, 'cardboard')

    def test_document_has_layers_in_execution_order_with_presets(self):
        g = plate.job_geometry(prepared(KEYRING))
        entries = [dict(id='a', name='Åse (4.A), Skilt', geometry=g, x=10, y=10, rotation=90)]
        material = plate.material_by_id(self.presets, 'wood_3mm')
        root = ET.fromstring(plate.build_bvg(entries, material))
        self.assertEqual((root.get('width'), root.get('height'), root.get('data-workarea')), ('6000', '3750', 'fbb2'))
        layers = root.findall(f'{NS}g')
        # Cutting first in the file = bottom of Beam Studio's list = runs last.
        self.assertEqual([l.get('data-configName') for l in layers], ['wood_3mm_cutting', 'wood_engraving'])
        cut = layers[0]
        self.assertEqual((cut.get('data-speed'), cut.get('data-strength'), cut.get('data-module')), ('15', '15', '15'))
        self.assertEqual(len(cut.findall(f'{NS}path')), 2)
        self.assertTrue(all(p.get('fill') == 'none' for p in cut.findall(f'{NS}path')))

    def test_engraving_only_material_refuses_cuts(self):
        g = plate.job_geometry(prepared(KEYRING))
        with self.assertRaises(ValueError):
            plate.build_bvg([dict(id='a', name='A', geometry=g, x=10, y=10, rotation=0)], plate.material_by_id(self.presets, 'slate'))


@unittest.skipUnless(FluxGhost().available(), 'Beam Studio er ikke installeret')
class FluxGhostTests(unittest.TestCase):
    def test_flux_builds_the_plate_where_it_was_placed(self):
        g = plate.job_geometry(prepared(KEYRING))
        entries = [dict(id='a', name='A', geometry=g, x=100, y=50, rotation=0),
                   dict(id='b', name='B', geometry=g, x=200, y=120, rotation=90)]
        document = plate.build_bvg(entries, plate.material_by_id(plate.load_presets(), 'wood_3mm'))
        with tempfile.TemporaryDirectory() as temp:
            ghost = FluxGhost(workdir=temp)
            try:
                self.assertIn('fluxghost', ghost.version())
                task = ghost.task(document)
            finally:
                ghost.stop()
        self.assertGreater(task['seconds'], 1)
        self.assertGreater(task['bytes'], 100)
        meta = {k: float(task['metadata'][k]) for k in ('min_x', 'min_y', 'max_x', 'max_y')}
        expected = plate.expected_extent(entries)
        self.assertAlmostEqual(meta['min_x'], expected[0], delta=0.1)
        self.assertAlmostEqual(meta['min_y'], expected[1], delta=0.1)
        self.assertAlmostEqual(meta['max_x'], expected[2], delta=0.1)
        self.assertAlmostEqual(meta['max_y'], expected[3], delta=0.1)


class FakeGhost:
    def __init__(self):
        self.documents = []

    def available(self):
        return True

    def task(self, document, model='fbb2'):
        self.documents.append(document)
        return {'seconds': 42.0, 'bytes': 1000, 'metadata': {'min_x': '1', 'min_y': '2', 'max_x': '3', 'max_y': '4'}, 'fcode': b''}


class StationHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        data = Path(self.temp.name) / 'queue'
        self.student = create_server(data, port=0, code=CODE)
        self.teacher = create_server(data, port=0, code=CODE, surface='teacher', inbox=self.student.inbox)
        for server in (self.student, self.teacher):
            server.RequestHandlerClass.log_message = lambda *a: None
            threading.Thread(target=server.serve_forever, daemon=True).start()
        self.ids = []
        for name in ('Åse', 'Bo'):
            result = self.student.inbox.submit({'elevnavn': name, 'klasse': '4.A', 'projekt': 'Skilt', 'requestId': str(uuid.uuid4()), 'svg': KEYRING})
            self.ids.append(result['id'])
        self.opened = []
        self.ghost = FakeGhost()
        queue = QueueClient(f'http://127.0.0.1:{self.teacher.server_port}')
        self.station = Station(queue, Path(self.temp.name) / 'plader', ghost=self.ghost, opener=self.opened.append)
        self.server = create_station_server(self.station, 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_port

    def tearDown(self):
        for server in (self.server, self.student, self.teacher):
            server.shutdown()
            server.server_close()
        self.temp.cleanup()

    def request(self, route, data=None, origin=True, host=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        headers = {'Host': host or f'127.0.0.1:{self.port}'}
        if data is not None:
            headers['Content-Type'] = 'application/json'
            if origin:
                headers['Origin'] = f'http://127.0.0.1:{self.port}'
        conn.request('POST' if data is not None else 'GET', route, json.dumps(data) if data is not None else None, headers)
        response = conn.getresponse()
        body = response.read()
        conn.close()
        try:
            return response.status, json.loads(body)
        except ValueError:
            return response.status, body

    def login(self):
        status, _ = self.request('/api/station/login', {'code': CODE})
        self.assertEqual(status, 200)

    def test_page_and_state_without_login(self):
        status, body = self.request('/')
        self.assertEqual(status, 200)
        self.assertIn(b'Laserstation', body)
        status, state = self.request('/api/station/state')
        self.assertEqual(status, 200)
        self.assertFalse(state['loggedIn'])
        self.assertIn('wood_3mm', [m['id'] for m in state['materials']])
        self.assertEqual(self.request('/api/station/jobs')[0], 401)

    def test_wrong_code_is_refused(self):
        status, body = self.request('/api/station/login', {'code': 'forkert'})
        self.assertEqual(status, 400)
        self.assertIn('passer ikke', body['error'])

    def test_foreign_origin_host_and_content_type_are_refused(self):
        self.assertEqual(self.request('/api/station/login', {'code': CODE}, origin=False)[0], 403)
        self.assertEqual(self.request('/api/station/state', host='evil.example:80')[0], 403)
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        conn.request('POST', '/api/station/logout', '{}', {'Host': f'127.0.0.1:{self.port}', 'Origin': f'http://127.0.0.1:{self.port}', 'Content-Type': 'text/plain'})
        self.assertEqual(conn.getresponse().status, 415)
        conn.close()

    def test_full_plate_flow_marks_jobs_done_on_the_server(self):
        self.login()
        status, body = self.request('/api/station/jobs')
        self.assertEqual(status, 200)
        self.assertEqual({j['id'] for j in body['jobs']}, set(self.ids))
        status, geometry = self.request(f'/api/station/jobs/{self.ids[0]}/geometry')
        self.assertEqual(geometry['bbox'], [20.0, 10.0, 80.0, 40.0])
        status, placed = self.request('/api/station/place', {'ids': self.ids})
        self.assertEqual(status, 200)
        self.assertEqual(placed['unplaced'], [])
        items = placed['placements']
        status, check = self.request('/api/station/plate/check', {'material': 'wood_3mm', 'items': items})
        self.assertEqual((status, check['problems'], check['flux']['seconds']), (200, [], 42.0))
        overlapping = [dict(items[0]), dict(items[1], x=items[0]['x'] + 5, y=items[0]['y'])]
        status, body = self.request('/api/station/plate/open', {'material': 'wood_3mm', 'items': overlapping})
        self.assertEqual(status, 400)
        self.assertIn('overlapper', body['error'])
        status, opened = self.request('/api/station/plate/open', {'material': 'wood_3mm', 'items': items})
        self.assertEqual(status, 200)
        self.assertTrue(opened['opened'])
        self.assertEqual(self.opened, [Path(opened['file'])])
        self.assertTrue(Path(opened['file']).read_text(encoding='utf-8').startswith('<svg'))
        record = json.loads(Path(opened['file']).with_suffix('.json').read_text(encoding='utf-8'))
        self.assertEqual(record['material'], 'wood_3mm')
        self.assertEqual(len(record['items']), 2)
        status, state = self.request('/api/station/state')
        self.assertEqual(set(state['lastPlate']['ids']), set(self.ids))
        # Only jobs from the latest plate can be marked from the station.
        self.assertEqual(self.request('/api/station/plate/done', {'ids': ['0' * 32]})[0], 400)
        status, done = self.request('/api/station/plate/done', {'ids': self.ids})
        self.assertEqual((status, done['marked']), (200, 2))
        self.assertEqual(self.request('/api/station/jobs')[1]['jobs'], [])
        statuses = {j['id']: j['status'] for j in self.student.inbox.jobs()}
        self.assertEqual(set(statuses.values()), {'done'})

    def test_engraving_only_material_is_refused_for_cuts(self):
        self.login()
        items = self.request('/api/station/place', {'ids': self.ids[:1]})[1]['placements']
        status, body = self.request('/api/station/plate/open', {'material': 'slate', 'items': items})
        self.assertEqual(status, 400)
        self.assertIn('kun graveres', body['error'])

    def test_logout_ends_the_queue_session(self):
        self.login()
        self.assertEqual(self.request('/api/station/logout', {})[0], 200)
        self.assertEqual(self.request('/api/station/jobs')[0], 401)


if __name__ == '__main__':
    unittest.main()
