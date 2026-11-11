import http.client
import base64
from io import BytesIO
import json
from pathlib import Path
import tempfile
import threading
import unittest
import uuid
import xml.etree.ElementTree as ET
from urllib.parse import urlencode
from app.laser_server import create_server, clean_svg, Inbox
from app.laser_station import QueueClient
from PIL import Image

SVG = '<svg xmlns="http://www.w3.org/2000/svg" data-laservaerksted="1" width="100mm" height="60mm" viewBox="0 0 100 60"><title>Navneskilt</title><g data-operation="cut"><path d="M10 10H60V40H10Z" data-operation="cut"/></g></svg>'

def image_svg(trace=True):
    output=BytesIO();Image.new('RGB',(20,10),(30,70,100)).save(output,format='PNG')
    href='data:image/png;base64,'+base64.b64encode(output.getvalue()).decode('ascii')
    path='<path d="M2 2H18V8H2Z M5 4H10V6H5Z"/>' if trace else ''
    return '<svg xmlns="http://www.w3.org/2000/svg" data-laservaerksted="1" width="100mm" height="60mm" viewBox="0 0 100 60"><g data-operation="engrave-fill"><g data-image="1" data-id="test-image-1" data-name="Ring" data-operation="engrave-fill" data-display="image"><image width="20" height="10" transform="matrix(2 0 0 2 10 10)" href="'+href+'"/>'+path+'</g></g><g data-operation="cut"><path data-operation="cut" d="M2 2H90V50H2Z"/></g></svg>'


class LaserInboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.student = create_server(self.temp.name,port=0,code='test-only-code')
        self.teacher = create_server(self.temp.name,port=0,inbox=self.student.inbox,surface='teacher')
        for server in (self.student,self.teacher):
            server.RequestHandlerClass.log_message=lambda *args:None
            threading.Thread(target=server.serve_forever,daemon=True).start()
        self.cookie = ''

    def tearDown(self):
        for server in (self.student,self.teacher):
            server.shutdown()
            server.server_close()
        self.temp.cleanup()

    def request(self, route, data=None, teacher=False, cookie='', origin=None):
        server = self.teacher if teacher else self.student
        conn = http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
        headers = {'Content-Type':'application/json'}
        if cookie:
            headers['Cookie']=cookie
        if origin:
            headers['Origin']=origin
        conn.request('POST' if data is not None else 'GET',route,json.dumps(data) if data is not None else None,headers)
        response = conn.getresponse()
        body = response.read()
        status,headers = response.status,dict(response.getheaders())
        conn.close()
        return status,headers,body

    def payload(self):
        return {'elevnavn':'Åse','klasse':'3.A','projekt':'Navneskilt','requestId':str(uuid.uuid4()),'svg':SVG}

    def login(self):
        status,headers,_=self.request('/api/laser/login',{'code':'test-only-code'},teacher=True)
        self.assertEqual(status,200)
        self.cookie=headers['Set-Cookie'].split(';')[0]

    def test_student_surface_has_no_teacher_pages_or_routes(self):
        self.login()
        for route in ['/laser-teacher.html','/laser-teacher.js','/laerer','/api/laser/jobs','/data/laser/laererkode.txt','/../app/laser_server.py']:
            self.assertEqual(self.request(route,cookie=self.cookie)[0],404,route)
        self.assertEqual(self.request('/api/laser/login',{'code':'test-only-code'})[0],404)
        self.assertNotIn('teacher',json.loads(self.request('/api/laser/health')[2]))
        self.assertEqual(self.request('/api/laser/submit',self.payload(),teacher=True)[0],404)

    def test_workshop_home_and_cutter_routes_stay_on_student_surface(self):
        status,_,body=self.request('/')
        self.assertEqual(status,200)
        self.assertIn(b'/folie.html',body)
        self.assertIn(b'/laser.html',body)
        self.assertNotIn(b'laser-teacher',body)
        for route in ['/workshop.html','/workshop.css','/folie.html','/laser.html']:
            self.assertEqual(self.request(route)[0],200,route)
            self.assertEqual(self.request(route,teacher=True)[0],404,route)
        self.assertIn(b'laser-teacher.js',self.request('/',teacher=True)[2])

    def test_teacher_files_and_queue_require_session(self):
        job=json.loads(self.request('/api/laser/submit',self.payload())[2])['id']
        self.assertEqual(self.request('/api/laser/jobs',teacher=True)[0],401)
        self.assertEqual(self.request(f'/api/laser/jobs/{job}/svg',teacher=True)[0],401)
        self.assertEqual(self.request(f'/api/laser/jobs/{job}/status',{'status':'done'},teacher=True)[0],401)
        self.assertEqual(self.request('/api/laser/login',{'code':'wrong'},teacher=True)[0],401)
        self.login()
        self.assertEqual(self.request('/api/laser/jobs',teacher=True,cookie=self.cookie)[0],200)
        self.request('/api/laser/logout',{},teacher=True,cookie=self.cookie)
        self.assertEqual(self.request('/api/laser/jobs',teacher=True,cookie=self.cookie)[0],401)

    def test_submission_download_status_and_persistence(self):
        payload=self.payload()
        curve='M10 10C20 0 50 20 60 10L60 40H10Z'
        payload['svg']=SVG.replace('M10 10H60V40H10Z',curve).replace('<path ','<path data-group="classroom-group-1" ')
        status,_,body=self.request('/api/laser/submit',payload)
        self.assertEqual(status,200)
        job=json.loads(body)['id']
        self.login()
        rows=json.loads(self.request('/api/laser/jobs',teacher=True,cookie=self.cookie)[2])['jobs']
        self.assertEqual(rows[0]['student'],'Åse')
        self.assertEqual(rows[0]['details']['operations']['cut'],1)
        status,headers,body=self.request(f'/api/laser/jobs/{job}/svg',teacher=True,cookie=self.cookie)
        self.assertEqual(status,200)
        self.assertEqual(ET.fromstring(body).get('width'),'100mm')
        path=ET.fromstring(body).find('.//{http://www.w3.org/2000/svg}path')
        self.assertEqual(path.get('d'),curve)
        self.assertEqual(path.get('data-group'),'classroom-group-1')
        self.assertEqual(headers['X-Content-Type-Options'],'nosniff')
        self.assertEqual(self.request(f'/api/laser/jobs/{job}/status',{'status':'ready'},teacher=True,cookie=self.cookie)[0],200)
        reopened=Inbox(self.temp.name,code='test-only-code')
        self.assertEqual(reopened.jobs()[0]['status'],'ready')

    def test_retry_does_not_duplicate_and_cannot_replace_work(self):
        payload=self.payload()
        first=json.loads(self.request('/api/laser/submit',payload)[2])
        retry=json.loads(self.request('/api/laser/submit',payload)[2])
        self.assertEqual(first['id'],retry['id'])
        self.assertTrue(retry['duplicate'])
        payload['elevnavn']='En anden'
        self.assertEqual(self.request('/api/laser/submit',payload)[0],400)
        self.assertEqual(len(self.student.inbox.jobs()),1)

    def test_done_history_does_not_fill_submission_capacity(self):
        payload = self.payload()
        first = self.student.inbox.submit(payload)
        with self.student.inbox.db() as db:
            db.execute("UPDATE jobs SET status='done', svg=NULL, payload_available=0 WHERE id=?", (first['id'],))
            db.executemany(
                'INSERT INTO jobs (id,request_id,fingerprint,created_at,student,classroom,project,status,svg,details,payload_available) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                ((f'{i:032x}', f'done-history-{i}', f'fingerprint-{i}', '2026-01-01T00:00:00+00:00',
                  'Elev', '3.A', 'Historik', 'done', None, '{}', 0) for i in range(4999)))

        duplicate = self.student.inbox.submit(payload)
        self.assertTrue(duplicate['duplicate'])
        fresh = dict(payload, requestId='new-capacity-request-0001')
        self.assertFalse(self.student.inbox.submit(fresh)['duplicate'])

    def test_station_summary_keeps_large_image_queue_below_client_limit(self):
        images = [{'id': f'{i:04d}' + 'i' * 76, 'name': 'n' * 60, 'operation': 'engrave-fill', 'hasTrace': True}
                  for i in range(499)]
        details = json.dumps({'operations': {'engrave-fill': len(images)}, 'images': images})
        with self.student.inbox.db() as db:
            db.executemany(
                'INSERT INTO jobs (id,request_id,fingerprint,created_at,student,classroom,project,status,svg,details,payload_available) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                ((f'{i:032x}', f'summary-large-{i}', f'fingerprint-{i}', '2026-01-01T00:00:00+00:00',
                  'Elev', '3.A', f'Historik {i}', 'done' if i % 2 else 'ready', None, details, 0 if i % 2 else 1)
                 for i in range(80)))
        self.assertEqual(self.request('/api/laser/jobs?summary=1&cursor=bad', teacher=True)[0], 401)
        self.login()
        self.assertEqual(self.request('/api/laser/jobs?summary=1&cursor=bad', teacher=True, cookie=self.cookie)[0], 400)
        summary, cursor, pages = [], None, 0
        while True:
            query = {'summary': '1', 'limit': '10'}
            if cursor is not None:
                query['cursor'] = cursor
            status, _, body = self.request('/api/laser/jobs?' + urlencode(query), teacher=True, cookie=self.cookie)
            self.assertEqual(status, 200)
            self.assertLess(len(body), 3_000_000)
            self.assertNotIn(b'"details"', body)
            page = json.loads(body)
            summary.extend(page['jobs'])
            pages += 1
            next_cursor = page.get('nextCursor')
            if next_cursor is None:
                break
            self.assertNotEqual(next_cursor, cursor)
            cursor = next_cursor
        self.assertGreater(pages, 3)
        self.assertEqual(len(summary), 80)
        active = next(job for job in summary if job['status'] == 'ready')
        self.assertTrue(active['needsChoice'])
        self.assertEqual(len(active['images']), 499)
        self.assertIn('station_id', active)
        done = next(job for job in summary if job['status'] == 'done')
        self.assertEqual(done['images'], [])
        self.assertFalse(done['needsChoice'])
        self.assertEqual(len(self.student.inbox.jobs()), 80)
        queue = QueueClient(f'http://127.0.0.1:{self.teacher.server_port}')
        queue.login('test-only-code')
        self.assertEqual(len(queue.jobs(summary=True)), 80)

    def test_cross_origin_posts_are_rejected(self):
        self.assertEqual(self.request('/api/laser/submit',self.payload(),origin='https://example.com')[0],403)

    def test_image_methods_require_teacher_and_change_only_prepared_export(self):
        payload=self.payload();payload['svg']=image_svg()
        status,_,body=self.request('/api/laser/submit',payload);self.assertEqual(status,200)
        job=json.loads(body)['id'];route=f'/api/laser/jobs/{job}/image-mode'
        choice={'imageId':'test-image-1','mode':'vector'}
        self.assertEqual(self.request(route,choice)[0],404)
        self.assertEqual(self.request(route,choice,teacher=True)[0],401)
        self.login()
        self.assertEqual(self.request(route,choice,cookie=self.cookie)[0],404)
        self.assertEqual(self.request(f'/api/laser/jobs/{job}/svg',teacher=True,cookie=self.cookie)[0],409)
        original=self.request(f'/api/laser/jobs/{job}/svg?source=1',teacher=True,cookie=self.cookie)[2]
        self.assertEqual(self.request(route,choice,teacher=True,cookie=self.cookie)[0],200)
        vector=self.request(f'/api/laser/jobs/{job}/svg',teacher=True,cookie=self.cookie)[2]
        v=ET.fromstring(vector);self.assertFalse(v.findall('.//{http://www.w3.org/2000/svg}image'))
        traced=v.find('.//{http://www.w3.org/2000/svg}path[@data-name="Ring"]');self.assertEqual(traced.get('fill'),'none');self.assertEqual(traced.get('data-operation'),'engrave-line');self.assertEqual(traced.get('transform'),'matrix(2 0 0 2 10 10)')
        self.assertIsNotNone(v.find('.//{http://www.w3.org/2000/svg}path[@data-operation="cut"]'))
        choice['mode']='raster';self.assertEqual(self.request(route,choice,teacher=True,cookie=self.cookie)[0],200)
        raster=ET.fromstring(self.request(f'/api/laser/jobs/{job}/svg',teacher=True,cookie=self.cookie)[2])
        self.assertEqual(len(raster.findall('.//{http://www.w3.org/2000/svg}image')),1)
        self.assertEqual(len(raster.findall('.//{http://www.w3.org/2000/svg}path')),1)
        self.assertEqual(original,self.request(f'/api/laser/jobs/{job}/svg?source=1',teacher=True,cookie=self.cookie)[2])
        summary = json.loads(self.request('/api/laser/jobs?summary=1',teacher=True,cookie=self.cookie)[2])
        image = next(item for item in summary['jobs'][0]['images'] if item['id'] == 'test-image-1')
        self.assertEqual(image['mode'], 'raster')
        choice['mode']='vector';self.assertEqual(self.request(route,choice,teacher=True,cookie=self.cookie)[0],200)
        summary = json.loads(self.request('/api/laser/jobs?summary=1',teacher=True,cookie=self.cookie)[2])
        image = next(item for item in summary['jobs'][0]['images'] if item['id'] == 'test-image-1')
        self.assertEqual(image['mode'], 'vector')
        reopened=Inbox(self.temp.name,code='test-only-code');self.assertEqual(reopened.jobs()[0]['imageModes'],{'test-image-1':'vector'})

    def test_image_validation_and_missing_trace(self):
        for svg in [image_svg().replace('data:image/png;base64,','https://example.com/'),image_svg().replace('width="20"','width="9999"'),image_svg().replace('matrix(2 0 0 2 10 10)','matrix(0 0 0 0 0 0)'),image_svg().replace('<image ','<script/><image ')]:
            with self.assertRaises(ValueError):clean_svg(svg)
        payload=self.payload();payload['svg']=image_svg(False);job=json.loads(self.request('/api/laser/submit',payload)[2])['id'];self.login()
        self.assertEqual(self.request(f'/api/laser/jobs/{job}/image-mode',{'imageId':'test-image-1','mode':'vector'},teacher=True,cookie=self.cookie)[0],400)
        self.assertEqual(self.request(f'/api/laser/jobs/{job}/image-mode',{'imageId':'test-image-1','mode':'raster'},teacher=True,cookie=self.cookie)[0],200)

    def test_server_rebuilds_svg_and_rejects_embedded_content(self):
        augmented=SVG.replace('<path ','<path onclick="alert(1)" style="filter:url(https://example.com/x)" ')
        cleaned,_,_=clean_svg(augmented)
        self.assertNotIn('onclick',cleaned)
        self.assertNotIn('example.com',cleaned)
        for source in [SVG.replace('<title>','<script/> <title>'), SVG.replace('data-operation="cut"','data-operation="undecided"'), SVG.replace('<path ','<image '), SVG.replace('100mm','100px'), '<!DOCTYPE svg [<!ENTITY x "hello">]>'+SVG]:
            with self.assertRaises(ValueError):
                clean_svg(source)


if __name__ == '__main__':
    unittest.main()
