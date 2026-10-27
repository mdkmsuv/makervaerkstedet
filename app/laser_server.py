"""Local classroom SVG inbox. Contains no printer or laser communication."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import ipaddress
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
import unicodedata
from urllib.parse import urlparse, unquote, parse_qs
import uuid
import xml.etree.ElementTree as ET
from app.instance import InstanceLock
from app.backup_guard import assert_not_restored, write_protocol
from app.school_appearance import (school_appearance, school_appearance_asset,
                                  school_appearance_manifest_present,
                                  render_local_html)
from app.project_origins import DEFAULT_PROJECT_ORIGINS, validate_project_origins, configured_project_origins
from app.demo_workshop import demo_component
from app.laser_images import clean_motif, image_details, prepared_svg
from app.laser_workflows import LaserWorkflows
from app.printer_config import ConfigConflict
from app.laser_inbox_lifecycle import (cleanup_terminal_payload, cleanup_terminal_payloads,
                                        delete_expired_history, enable_incremental_autovacuum,
                                        ensure_payload_column)
from app.generation_runtime import child_command, child_cwd

ROOT = Path(__file__).resolve().parent.parent
NS = 'http://www.w3.org/2000/svg'
ET.register_namespace('', NS)
OPERATIONS = ('engrave-fill', 'engrave-line', 'cut')
STATUSES = ('received', 'ready', 'done')
MAX_BODY = 2_500_000
MAX_QUEUE_JOBS = 5000
MAX_QUEUE_BYTES = 1_000_000_000  # pending SVG (UTF-8); keeps a flood from filling the shared server disk
MAX_SENDER_JOBS = 10
MAX_SENDER_BYTES = 10_000_000
HISTORY_SWEEP_SECONDS = 3600
SUMMARY_PAGE_SIZE = 10
SUMMARY_MAX_BYTES = 2_800_000
BOX_MAX_SECONDS = 5
BOX_MAX_RESPONSE = 2_000_000


def generate_box_svg_timed(data):
    """Run the bounded generator in a killable one-shot process.

    A subprocess keeps a malformed dependency or future generator change from
    pinning a ThreadingHTTPServer worker forever. It only communicates over
    stdin/stdout and never receives a filesystem output path.
    """
    process = subprocess.Popen(
        child_command('app.boxes_worker', []),
        cwd=child_cwd(ROOT),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    payload = json.dumps(data, ensure_ascii=False).encode('utf-8')
    try:
        output, _error = process.communicate(payload, timeout=BOX_MAX_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise TimeoutError('Kassegeneratoren brugte for lang tid. Prøv mindre mål.') from None
    if process.returncode:
        raise ValueError('Kassegeneratoren kunne ikke lave en gyldig kasse.')
    if len(output) > BOX_MAX_RESPONSE:
        raise ValueError('Kassegeneratorens svar blev for stort.')
    try:
        result = json.loads(output.decode('utf-8'))
        svg, details = result['svg'], result['details']
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
        raise ValueError('Kassegeneratoren svarede ugyldigt.') from None
    if not isinstance(svg, str) or not isinstance(details, dict):
        raise ValueError('Kassegeneratoren svarede ugyldigt.')
    return svg, details


def field(data, key, limit):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'Udfyld {key} med højst {limit} tegn.')
    if any(unicodedata.category(ch) in ('Cc', 'Cs') for ch in value):
        raise ValueError(f'Feltet {key} skal være én linje uden kontroltegn.')
    return value.strip()


def sender_key(student, classroom):
    """Treat spelling/case/spacing variants as the same classroom quota.

    These pupil-entered fields are a fairness limit, not authenticated identity.
    The independent global queue limit still bounds the whole service.
    """
    return tuple(' '.join(unicodedata.normalize('NFKC', value).split()).casefold()
                 for value in (student, classroom))


def clean_svg(source):
    """Rebuild our outlined SVG dialect. No client XML is served unchanged."""
    if not isinstance(source, str) or len(source.encode('utf-8')) > 2_000_000:
        raise ValueError('Tegningen er for stor.')
    if re.search(r'<!DOCTYPE|<!ENTITY|<\?xml-stylesheet', source, re.I):
        raise ValueError('SVG med eksternt indhold kan ikke afleveres.')
    try:
        root = ET.fromstring(source)
    except ET.ParseError:
        raise ValueError('SVG-filen kunne ikke læses.') from None
    if root.tag != f'{{{NS}}}svg' or root.get('data-laservaerksted') != '1':
        raise ValueError('Åbn SVG-filen i Laserværksted og vælg Skær eller Gravér først.')
    dimensions = []
    for key in ('width', 'height'):
        match = re.fullmatch(r'(\d+(?:\.\d+)?)mm', root.get(key, ''))
        number = float(match[1]) if match else 0
        if not 1 <= number <= 600:
            raise ValueError('Tegningen skal have mål i millimeter, højst 600 × 600 mm.')
        dimensions.append(number)
    if root.get('viewBox', '').split() != ['0', '0', *(root.get(k)[:-2] for k in ('width', 'height'))]:
        raise ValueError('SVG-arbejdsområdet passer ikke til tegningens mål.')
    tag = lambda name: f'{{{NS}}}{name}'
    safe = ET.Element(tag('svg'), {'version':'1.1','width':root.get('width'),'height':root.get('height'),'viewBox':root.get('viewBox'),'data-laservaerksted':'1'})
    title = ET.SubElement(safe, tag('title'))
    title.text = (root.findtext(tag('title')) or 'Min tegning')[:60]
    grouped = {op: [] for op in OPERATIONS}
    count = 0
    image_ids = set()
    for node in root:
        if node.tag in (tag('title'), tag('desc')):
            continue
        if node.tag != tag('g') or node.get('data-operation') not in OPERATIONS:
            raise ValueError('Alle dele skal have et valg: Skær eller Gravér.')
        op = node.get('data-operation')
        for p in node:
            count += 1
            if count<=500 and p.tag==tag('g') and p.get('data-image')=='1' and p.get('data-operation')==op:
                motif=clean_motif(p,op)
                if motif.get('data-id') in image_ids:raise ValueError('To billeder har samme nummer.')
                image_ids.add(motif.get('data-id'));grouped[op].append(motif);continue
            d = p.get('d', '')
            if count > 500 or p.tag != tag('path') or list(p) or p.get('data-operation') != op:
                raise ValueError('Afleveringen indeholder ugyldige dele.')
            if len(d) > 150000 or not re.fullmatch(r'[\s\d.,+eEaAcChHlLmMqQsStTvVzZ-]+', d) or not re.match(r'\s*[Mm]', d):
                raise ValueError('En kontur kunne ikke læses.')
            numbers = re.findall(r'[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?', d)
            if not numbers or any(not math.isfinite(float(n)) or abs(float(n)) > 100000 for n in numbers):
                raise ValueError('En kontur har ugyldige koordinater.')
            attrs = {'d':d, 'data-operation':op,'data-name':p.get('data-name','Figur')[:60], 'fill-rule':'evenodd' if p.get('fill-rule') == 'evenodd' else 'nonzero'}
            if re.fullmatch(r'[a-zA-Z0-9-]{1,80}',p.get('data-group','')):
                attrs['data-group'] = p.get('data-group')
            # Keep our editor's optional text/gear values, never arbitrary SVG attributes.
            for key in ('text', 'gear'):
                try:
                    obj = json.loads(p.get('data-' + key, 'null'))
                    if key == 'text' and isinstance(obj, dict) and isinstance(obj.get('value'), str) and 1 <= len(obj['value']) <= 80 and isinstance(obj.get('size'), (int, float)) and 2 <= obj['size'] <= 60:
                        text_data = {'value':obj['value'],'size':obj['size']}
                        matrix = obj.get('matrix')
                        if 'matrix' in obj:
                            if (not isinstance(matrix, list) or len(matrix) != 4
                                    or any(type(n) not in (int, float) or not math.isfinite(n) or abs(n) > 100000 for n in matrix)
                                    or abs(matrix[0]*matrix[3]-matrix[1]*matrix[2]) < 1e-12):
                                continue
                            text_data['matrix'] = matrix
                        attrs['data-text'] = json.dumps(text_data,ensure_ascii=False)
                    if key == 'gear' and isinstance(obj, dict) and isinstance(obj.get('teeth'), int) and 17 <= obj['teeth'] <= 48 and isinstance(obj.get('hole'), (int, float)) and 0 <= obj['hole'] <= 12:
                        attrs['data-gear'] = json.dumps({'teeth':obj['teeth'],'hole':obj['hole']})
                except (ValueError, TypeError):
                    pass
            grouped[op].append(attrs)
    if count == 0:
        raise ValueError('Tilføj mindst én figur.')
    for op in OPERATIONS:
        if not grouped[op]:
            continue
        fill = op == 'engrave-fill'
        color = '#b4121a' if op == 'cut' else '#256b93'
        group = ET.SubElement(safe, tag('g'), {'id':op,'data-operation':op,'fill':color if fill else 'none','stroke':'none' if fill else color,'stroke-width':'0.3' if op == 'engrave-line' else '0.1'})
        for attrs in grouped[op]:
            if isinstance(attrs,ET.Element):group.append(attrs)
            else:ET.SubElement(group, tag('path'), attrs)
    return ET.tostring(safe, encoding='unicode'), dimensions, {op:len(grouped[op]) for op in OPERATIONS}


class Inbox:
    def __init__(self, folder, code=None):
        self.folder = Path(folder)
        component = demo_component(self.folder)
        if component is not None and component != 'laser':
            raise ValueError('Forkert DEMO-komponent til laserkøen.')
        self.simulation = component == 'laser'
        self.folder.mkdir(parents=True, exist_ok=True)
        self.db_path = self.folder / 'afleveringer.sqlite3'
        fresh_database = not self.db_path.exists()
        with self.db() as db:
            database_version = db.execute('PRAGMA user_version').fetchone()[0]
            if database_version not in (0, 1):
                raise ValueError('Uventet laserkø-databaseversion.')
            if fresh_database:
                # Keep future payload deletion cheap without copying the
                # complete inbox database on a space-constrained server.
                db.execute('PRAGMA auto_vacuum=INCREMENTAL')
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, request_id TEXT UNIQUE, fingerprint TEXT, created_at TEXT, student TEXT, classroom TEXT, project TEXT, status TEXT, svg TEXT, details TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS image_settings (job_id TEXT PRIMARY KEY, modes TEXT NOT NULL)')
            ensure_payload_column(db)
        code_file = self.folder / 'laererkode.txt'
        if code is None:
            if not code_file.exists():
                code_file.write_text(secrets.token_hex(5), encoding='ascii')
            code = code_file.read_text(encoding='ascii').strip()
        self.code = code
        self.workflows = LaserWorkflows(self)
        self.workflows.initialize()
        with self.db() as db:
            cleanup_terminal_payloads(db)
            delete_expired_history(db)
            # Existing databases use SQLite's default mode. This one-time
            # migration is best effort and never blocks the inbox if VACUUM
            # cannot obtain temporary space or the database is locked.
            enable_incremental_autovacuum(db)
            db.execute('PRAGMA user_version=1')
        key_file = self.folder / 'makerspace-laser.key'
        if key_file.is_symlink():
            raise ValueError('Laseradministrationens nøgle må ikke være et symbolsk link.')
        if not key_file.exists():
            descriptor = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w', encoding='ascii') as stream:
                stream.write('mkbridge_' + secrets.token_urlsafe(32))
        if key_file.stat().st_size > 128:
            raise ValueError('Ugyldig lokal lasernøgle.')
        self.makerspace_key = key_file.read_text(encoding='ascii').strip()
        if not re.fullmatch(r'mkbridge_[A-Za-z0-9_-]{43}', self.makerspace_key):
            raise ValueError('Ugyldig lokal lasernøgle.')
        if os.name != 'nt':
            key_file.chmod(0o600)
        self.sessions = {}
        self.attempts = {}
        self.lock = threading.Lock()
        self.last_sweep = time.monotonic()

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.db_path, timeout=10)
        # Deleted names and drawings are overwritten, not left in free space (GDPR).
        connection.execute('PRAGMA secure_delete=ON')
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def submit(self, data):
        student, classroom, project = (field(data,k,n) for k,n in [('elevnavn',60),('klasse',40),('projekt',60)])
        request_id = field(data, 'requestId', 80)
        if not re.fullmatch(r'[a-zA-Z0-9-]{16,80}', request_id):
            raise ValueError('Ugyldigt afleveringsnummer.')
        svg, dimensions, operations = clean_svg(data.get('svg'))
        fingerprint = hashlib.sha256(json.dumps([student,classroom,project,svg],ensure_ascii=False).encode()).hexdigest()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT id, fingerprint FROM jobs WHERE request_id=?',(request_id,)).fetchone()
            if old:
                if old[1] != fingerprint:
                    raise ValueError('Tegningen blev ændret. Start en ny aflevering.')
                return {'id':old[0], 'duplicate':True}
            # octet_length reads only the stored size, not the whole drawing.
            count, size = db.execute("SELECT count(*), coalesce(sum(octet_length(svg)),0) FROM jobs WHERE status IN ('received','ready') AND payload_available=1").fetchone()
            if count >= MAX_QUEUE_JOBS or size + len(svg.encode('utf-8')) > MAX_QUEUE_BYTES:
                raise ValueError('Afleveringskøen er fuld. Gem SVG og kontakt læreren.')
            # Keep retries above the quota check. A lost response must remain
            # recoverable even when this pupil's pending submissions are full.
            # Read only names and stored byte lengths, never all pending SVGs.
            sender = sender_key(student, classroom)
            sender_count = sender_bytes = 0
            for row in db.execute("SELECT student,classroom,count(*),coalesce(sum(octet_length(svg)),0) FROM jobs WHERE status IN ('received','ready') AND payload_available=1 GROUP BY student,classroom"):
                if sender_key(row[0], row[1]) == sender:
                    sender_count += row[2]
                    sender_bytes += row[3]
            if sender_count >= MAX_SENDER_JOBS or sender_bytes + len(svg.encode('utf-8')) > MAX_SENDER_BYTES:
                raise ValueError('Du har flere afleveringer, der venter på læreren. Gem SVG-filen og bed læreren afslutte de gamle afleveringer, før du sender flere.')
            job_id = uuid.uuid4().hex
            db.execute('INSERT INTO jobs (id,request_id,fingerprint,created_at,student,classroom,project,status,svg,details,payload_available) VALUES (?,?,?,?,?,?,?,?,?,?,1)',(job_id, request_id, fingerprint, datetime.now(timezone.utc).isoformat(),student,classroom,project,'received',svg,json.dumps({'width':dimensions[0],'height':dimensions[1],'operations':operations,'images':image_details(svg)})))
        return {'id':job_id, 'duplicate':False}

    def jobs(self):
        if time.monotonic() - self.last_sweep >= HISTORY_SWEEP_SECONDS:
            # The server runs for weeks; do not rely on a restart or a new 'done'.
            self.last_sweep = time.monotonic()
            with self.db() as db:
                delete_expired_history(db)
        with self.db() as db:
            db.row_factory = sqlite3.Row
            return [dict(r,details=json.loads(r['details']),imageModes=json.loads(r['imageModes'] or '{}'),payload_available=bool(r['payload_available'])) for r in db.execute('SELECT id,created_at,student,classroom,project,status,details,payload_available,modes AS imageModes FROM jobs LEFT JOIN image_settings ON jobs.id=image_settings.job_id ORDER BY created_at DESC')]

    @staticmethod
    def _summary_cursor(cursor):
        if cursor is None:
            return None
        if not isinstance(cursor, str) or len(cursor) > 180 or cursor.count('|') != 1:
            raise ValueError('Ugyldig kømarkør.')
        created, job_id = cursor.split('|', 1)
        try:
            datetime.fromisoformat(created)
        except (TypeError, ValueError):
            raise ValueError('Ugyldig kømarkør.') from None
        if not re.fullmatch(r'[a-f0-9]{32}', job_id):
            raise ValueError('Ugyldig kømarkør.')
        return created, job_id

    def summary_page(self, limit=SUMMARY_PAGE_SIZE, cursor=None):
        if type(limit) is not int or not 1 <= limit <= SUMMARY_PAGE_SIZE:
            raise ValueError('Køsiden skal være mellem 1 og 10 job.')
        cursor = self._summary_cursor(cursor)
        if time.monotonic() - self.last_sweep >= HISTORY_SWEEP_SECONDS:
            self.last_sweep = time.monotonic()
            with self.db() as db:
                delete_expired_history(db)
        with self.db() as db:
            db.row_factory = sqlite3.Row
            where = ''
            params = []
            if cursor:
                where = ' WHERE created_at < ? OR (created_at = ? AND id < ?)'
                params.extend((cursor[0], cursor[0], cursor[1]))
            params.append(limit + 1)
            rows = db.execute(
                'SELECT id,created_at,student,classroom,project,status,details,payload_available,modes AS imageModes,'
                'station_id,workflow_authorized,handoff_claimed,workflow_complete,workflow_step,workflow_version '
                'FROM jobs LEFT JOIN image_settings ON jobs.id=image_settings.job_id' + where +
                ' ORDER BY created_at DESC,id DESC LIMIT ?', params).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        result = []
        for row in rows:
            details = json.loads(row['details']) or {}
            payload_available = bool(row['payload_available'])
            active = row['status'] != 'done' and payload_available
            modes = json.loads(row['imageModes'] or '{}')
            images = []
            if active:
                images = [{'id': image['id'], 'name': image.get('name') or 'Billede',
                           'hasTrace': bool(image.get('hasTrace')), 'mode': modes.get(image['id'])}
                          for image in details.get('images') or [] if image.get('operation') != 'cut']
            result.append({
                'id': row['id'], 'student': row['student'], 'classroom': row['classroom'],
                'project': row['project'], 'status': row['status'], 'created': row['created_at'],
                'operations': details.get('operations') or {}, 'images': images,
                'needsChoice': active and any(image.get('mode') is None for image in images),
                'payload_available': payload_available, 'station_id': row['station_id'],
                'workflow_authorized': bool(row['workflow_authorized']),
                'handoff_claimed': bool(row['handoff_claimed']), 'workflow_complete': bool(row['workflow_complete']),
                'workflow_step': row['workflow_step'], 'workflow_version': row['workflow_version'],
            })
        next_cursor = f'{rows[-1]["created_at"]}|{rows[-1]["id"]}' if more else None
        body = {'jobs': result, 'nextCursor': next_cursor}
        if len(json.dumps(body, ensure_ascii=False).encode('utf-8')) > SUMMARY_MAX_BYTES:
            raise ValueError('Køsiden er for stor selv i den begrænsede side. Reducér billedmængden på jobbet.')
        return body

    def svg(self, job_id):
        with self.db() as db:
            row = db.execute('SELECT svg,payload_available FROM jobs WHERE id=?',(job_id,)).fetchone()
        return row[0] if row and row[1] else None

    def status(self, job_id, status, actor='teacher:' + '0' * 32):
        if status not in STATUSES:
            raise ValueError('Vælg Modtaget, Klar eller Færdig.')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if self.workflows.legacy_status(job_id, status, actor, db=db):
                return
            row = db.execute('SELECT status FROM jobs WHERE id=?',(job_id,)).fetchone()
            if not row:
                raise ValueError('Afleveringen findes ikke.')
            if row[0] == 'done' and status != 'done':
                raise ValueError('Afleveringen er afsluttet og ligger kun som historik.')
            finished = datetime.now(timezone.utc).isoformat() if status == 'done' else None
            if not db.execute('UPDATE jobs SET status=?, finished_at=coalesce(finished_at, ?) WHERE id=?',(status,finished,job_id)).rowcount:
                raise ValueError('Afleveringen findes ikke.')
        if status == 'done':
            with self.db() as db:
                cleanup_terminal_payload(db, job_id)
                delete_expired_history(db)

    def image_mode(self, job_id, image_id, mode):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT svg,payload_available,workflow,workflow_step FROM jobs WHERE id=?',(job_id,)).fetchone()
            if not row:raise ValueError('Afleveringen findes ikke.')
            if not row[1] or row[0] is None:raise ValueError('Filen ligger ikke længere i afleveringskøen.')
            if row[2]:
                steps = json.loads(row[2])['definition']['steps']
                if any(step['action'] == 'approve' for step in steps[:row[3]]):
                    raise ValueError('Graveringsmetoden er låst efter workflowets godkendelse. Annullér og aflever på ny for at ændre den.')
            image=next((i for i in image_details(row[0]) if i['id']==image_id),None)
            if not image or image['operation']=='cut' or mode not in ('raster','vector') or mode=='vector' and not image['hasTrace']:
                raise ValueError('Vælg en gyldig graveringsmetode for billedet.')
            old=db.execute('SELECT modes FROM image_settings WHERE job_id=?',(job_id,)).fetchone()
            modes=json.loads(old[0]) if old else {};modes[image_id]=mode
            db.execute('INSERT INTO image_settings VALUES (?,?) ON CONFLICT(job_id) DO UPDATE SET modes=excluded.modes',(job_id,json.dumps(modes)))
        return modes

    def prepared(self,job_id,require_choices=True):
        with self.db() as db:
            row=db.execute('SELECT svg,modes,payload_available FROM jobs LEFT JOIN image_settings ON jobs.id=image_settings.job_id WHERE jobs.id=?',(job_id,)).fetchone()
        return prepared_svg(row[0],json.loads(row[1] or '{}'),require_choices) if row and row[2] and row[0] else None

    def validate_prepared(self, db, job_id):
        """Validate the current image choices inside an existing workflow transaction."""
        row=db.execute('SELECT svg,modes,payload_available FROM jobs LEFT JOIN image_settings ON jobs.id=image_settings.job_id WHERE jobs.id=?',(job_id,)).fetchone()
        if not row or not row[2] or not row[0]:
            raise ValueError('Filen ligger ikke længere i afleveringskøen.')
        return prepared_svg(row[0],json.loads(row[1] or '{}'),True)


class Handler(BaseHTTPRequestHandler):
    server_version = 'Laservaerksted'

    @property
    def inbox(self):
        return self.server.inbox

    def setup(self):
        super().setup()
        self.connection.settimeout(45)

    def reply(self, status, body, mime='application/json; charset=utf-8', headers=None):
        if body is None:
            body = 'null'
        if isinstance(body,(dict,list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body,str):
            body = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','same-origin')
        # A local pupil workshop is the same-origin gateway for the project
        # service. Its server instance may override this explicitly; retain a
        # conservative default for older callers.
        ancestors = "'none'" if self.server.surface == 'teacher' else getattr(self.server, 'frame_ancestors', None)
        if ancestors is None:
            ancestors = ' '.join(self.server.project_origins) or "'none'"
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; font-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors " + ancestors + "; base-uri 'none'; form-action 'self'")
        for name,value in (headers or {}).items():
            self.send_header(name,value)
        self.end_headers()
        self.wfile.write(body)

    def session(self):
        try:
            cookies = SimpleCookie(self.headers.get('Cookie',''))
            token = cookies['laser_teacher'].value if 'laser_teacher' in cookies else ''
        except Exception:
            token = ''
        with self.inbox.lock:
            return self.inbox.sessions.get(token,0) > time.monotonic()

    def teacher(self):
        if not self.session():
            self.reply(401,{'error':'Log ind med lærerkoden.'})
            return False
        return True

    def teacher_actor(self):
        cookies = SimpleCookie(self.headers.get('Cookie', ''))
        token = cookies['laser_teacher'].value if 'laser_teacher' in cookies else ''
        return 'teacher:' + hashlib.sha256(token.encode()).hexdigest()[:32]

    def require_makerspace(self):
        hosts = self.headers.get_all('Host', [])
        auth = self.headers.get_all('Authorization', [])
        try:
            local = ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            local = False
        if (self.server.surface != 'teacher' or not local or len(hosts) != 1
                or hosts[0] not in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')
                or self.headers.get_all('Cookie') or self.headers.get_all('Origin')
                or self.headers.get_all('X-Print-Source') or len(auth) != 1
                or not hmac.compare_digest(auth[0].encode(), ('Bearer ' + self.inbox.makerspace_key).encode())):
            raise PermissionError('Laseradministrationen kræver lokal tjenesteadgang.')

    def do_GET(self):
        route = unquote(urlparse(self.path).path)
        teacher_surface = self.server.surface == 'teacher'
        if route == '/api/laser/health':
            return self.reply(200,{'surface':'teacher','teacher':self.session()} if teacher_surface else {'surface':'student','inbox':True})
        if route == '/api/school-appearance':
            appearance = school_appearance(self.server.data_root)
            if appearance is None and school_appearance_manifest_present(self.server.data_root):
                return self.reply(503, {'error': 'Den lokale skoleprofil kunne ikke valideres.'})
            return self.reply(200, appearance)
        if route.startswith('/api/') and not teacher_surface:
            return self.reply(404,{'error':'Siden findes ikke.'})
        if route == '/api/makerspace/laser':
            try:
                self.require_makerspace()
                return self.reply(200, self.inbox.workflows.snapshot())
            except PermissionError as error:
                return self.reply(401, {'error': str(error)})
            except (sqlite3.Error, OSError):
                return self.reply(503, {'error': 'Laseradministrationen er midlertidigt utilgængelig.'})
        match = re.fullmatch(r'/api/laser/stations/([a-z0-9-]{1,40})/configuration', route)
        if match:
            if self.teacher():
                return self.reply(200, {'station': self.inbox.workflows.station(match[1])})
            return
        if route == '/api/laser/jobs':
            if self.teacher():
                query = parse_qs(urlparse(self.path).query)
                if query.get('summary') == ['1']:
                    try:
                        raw_limit = query.get('limit', [str(SUMMARY_PAGE_SIZE)])
                        raw_cursor = query.get('cursor', [None])
                        if len(raw_limit) != 1 or len(raw_cursor) != 1:
                            raise ValueError('Køparametrene må kun stå én gang.')
                        limit = int(raw_limit[0])
                        page = self.inbox.summary_page(limit=limit, cursor=raw_cursor[0])
                    except (TypeError, ValueError) as error:
                        return self.reply(400, {'error': str(error)})
                    return self.reply(200, page)
                self.reply(200, {'jobs': self.inbox.jobs()})
            return
        match = re.fullmatch(r'/api/laser/jobs/([a-f0-9]{32})/svg',route)
        if match:
            if not self.teacher():
                return
            query=parse_qs(urlparse(self.path).query)
            preview_requested=query.get('preview')==['1']
            try:svg = self.inbox.svg(match[1]) if query.get('source')==['1'] else self.inbox.prepared(match[1],not preview_requested)
            except ValueError as error:return self.reply(409,{'error':str(error)})
            if svg is None:
                return self.reply(404,{'error':'Afleveringen findes ikke.'})
            if preview_requested:
                preview = ET.fromstring(svg)
                for group in preview.findall(f'{{{NS}}}g'):
                    group.set('stroke-width','0.4')
                svg = ET.tostring(preview,encoding='unicode')
            headers = {'Content-Disposition':f'attachment; filename="tegning-{match[1][:8]}.svg"'} if query.get('download')==['1'] else None
            return self.reply(200,svg,'image/svg+xml',headers)
        if route == '/project-import-config.js' and not teacher_surface:
            origins = getattr(self.server, 'project_origins', DEFAULT_PROJECT_ORIGINS)
            script = ('Object.defineProperty(window, \"ElevhubProjectOrigins\", '
                      '{value: Object.freeze(' + json.dumps(origins) + ')});')
            return self.reply(200, script, 'text/javascript; charset=utf-8')
        # Only this app's public files are accessible. Never serve project data.
        if school_appearance_manifest_present(self.server.data_root):
            if school_appearance(self.server.data_root) is None:
                return self.reply(503, {'error': 'Den lokale skoleprofil kunne ikke valideres.'})
            asset = route.rsplit('/', 1)[-1] if route.startswith('/school-appearance/') else route
            local_asset = school_appearance_asset(self.server.data_root, asset)
            if local_asset is not None:
                return self.reply(200, local_asset[0], local_asset[1])
        route = ({'/':'/laser-teacher.html'} if teacher_surface else {'/':'/workshop.html','/folie.html':'/laser.html'}).get(route,route)
        surface_files = {'/laser-teacher.html','/laser-teacher.js','/laser-teacher.css'} if teacher_surface else {'/workshop.html','/workshop.css','/laser.html','/vendor/laser.js','/vendor/laser-trace-worker.js','/project-import.js'}
        allowed = route in surface_files | {'/laser.css','/laser-workshop.css','/vinyl-workshop.css','/desktop-studio.css','/school-theme.css','/school-theme.js','/favicon.svg','/assets/workshop-laser-v1.webp','/assets/workshop-vinyl-v1.webp','/assets/workshop-3d-bauhaus-v1.png','/assets/metrophobic-latin.ttf','/assets/klostermarksskolen-logo.png','/assets/Metrophobic-OFL.txt','/assets/Paper-MIT.txt','/assets/OpenType-MIT.txt','/assets/ImageTracer-UNLICENSE.txt','/assets/Clipper-LICENSE.txt'}
        file = ROOT / 'web' / route.lstrip('/')
        if not allowed or not file.is_file():
            return self.reply(404,{'error':'Siden findes ikke.'})
        content_type = mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
        body = file.read_bytes()
        if content_type.startswith('text/html'):
            body = render_local_html(body, school_appearance(self.server.data_root))
        self.reply(200, body, content_type)

    def do_POST(self):
        try:
            route = urlparse(self.path).path
            if route == '/api/makerspace/laser/commands':
                try:
                    self.require_makerspace()
                except PermissionError as error:
                    return self.reply(401, {'error': str(error)})
            lengths = self.headers.get_all('Content-Length', [])
            if len(lengths) != 1 or self.headers.get_all('Transfer-Encoding'):
                return self.reply(400, {'error': 'Brug én entydig forespørgsel.'})
            origin = self.headers.get('Origin')
            if origin and origin != 'http://' + self.headers.get('Host',''):
                return self.reply(403,{'error':'Aflever fra Laserværkstedets egen side.'})
            if self.headers.get_content_type() != 'application/json':
                return self.reply(415,{'error':'Brug Laserværkstedets formular.'})
            length = int(self.headers.get('Content-Length','0'))
            if not 0 < length <= MAX_BODY:
                return self.reply(413,{'error':'Afleveringen er for stor.'})
            data = json.loads(self.rfile.read(length))
            if not isinstance(data,dict):
                raise ValueError('Ugyldig forespørgsel.')
            teacher_surface = self.server.surface == 'teacher'
            student_routes = {'/api/laser/submit', '/api/laser/boxes'}
            if (not teacher_surface and route not in student_routes) or (teacher_surface and route in student_routes):
                return self.reply(404,{'error':'Funktionen findes ikke.'})
            if route == '/api/makerspace/laser/commands':
                try:
                    self.require_makerspace()
                except PermissionError as error:
                    return self.reply(401, {'error': str(error)})
                if set(data) != {'actor', 'envelope'}:
                    raise ValueError('Ugyldig laserkommando.')
                return self.reply(200, self.inbox.workflows.execute(data['actor'], data['envelope']))
            match = re.fullmatch(r'/api/laser/stations/([a-z0-9-]{1,40})/handoff', route)
            if match:
                if not self.teacher():
                    return
                if set(data) != {'model', 'ids'}:
                    raise ValueError('Vælg de aktuelle afleveringer og lasermodel.')
                return self.reply(200, self.inbox.workflows.check_export(match[1], data['model'], data['ids']))
            if route == '/api/laser/submit':
                return self.reply(200,self.inbox.submit(data))
            if route == '/api/laser/boxes':
                # The UI hides this behind Superbruger, but the server also
                # checks the level so a hand-written LAN request cannot turn
                # the classroom inbox into an unrestricted generator proxy.
                if data.get('level') != 3:
                    return self.reply(403,{'error':'Kasseværktøjet er kun åbent for Superbruger.'})
                try:
                    result, details = generate_box_svg_timed(data)
                except TimeoutError as error:
                    return self.reply(504,{'error':str(error)})
                return self.reply(200,{'svg':result,'details':details})
            if route == '/api/laser/login':
                now = time.monotonic()
                with self.inbox.lock:
                    attempts = [t for t in self.inbox.attempts.get(self.client_address[0],[]) if now-t < 60]
                    if len(attempts) >= 10:
                        return self.reply(429,{'error':'Vent et minut og prøv igen.'})
                    attempts.append(now)
                    self.inbox.attempts[self.client_address[0]] = attempts
                    if not hmac.compare_digest(str(data.get('code','')).encode(),self.inbox.code.encode()):
                        return self.reply(401,{'error':'Lærerkoden passer ikke.'})
                    self.inbox.sessions = {k:v for k,v in self.inbox.sessions.items() if v > now}
                    token = secrets.token_urlsafe(32)
                    self.inbox.sessions[token] = now + 8*3600
                return self.reply(200,{'ok':True},headers={'Set-Cookie':f'laser_teacher={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800'})
            if route == '/api/laser/logout':
                cookies = SimpleCookie(self.headers.get('Cookie',''))
                with self.inbox.lock:
                    if 'laser_teacher' in cookies:
                        self.inbox.sessions.pop(cookies['laser_teacher'].value,None)
                return self.reply(200,{'ok':True},headers={'Set-Cookie':'laser_teacher=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'})
            match = re.fullmatch(r'/api/laser/jobs/([a-f0-9]{32})/status',route)
            if match:
                if self.teacher():
                    self.inbox.status(match[1],data.get('status'), actor=self.teacher_actor())
                    self.reply(200,{'ok':True})
                return
            match = re.fullmatch(r'/api/laser/jobs/([a-f0-9]{32})/image-mode',route)
            if match:
                if self.teacher():self.reply(200,{'imageModes':self.inbox.image_mode(match[1],data.get('imageId'),data.get('mode'))})
                return
            self.reply(404,{'error':'Funktionen findes ikke.'})
        except ConfigConflict as error:
            self.reply(409, {'error': str(error)})
        except PermissionError as error:
            self.reply(403, {'error': str(error)})
        except (ValueError,UnicodeDecodeError) as error:
            self.reply(400,{'error':str(error)})
        except (sqlite3.Error, OSError):
            self.reply(503, {'error': 'Ændringen kunne ikke bekræftes. Genbrug samme request-ID.'})


def create_server(folder, host='127.0.0.1', port=8766, code=None, surface='student', inbox=None,
                  project_origins=None, project_origin_config=None):
    if project_origins is not None and project_origin_config is not None:
        raise ValueError('Vælg én konfiguration af projektserverens origins.')
    origins = (validate_project_origins(project_origins) if project_origins is not None
               else configured_project_origins(Path(project_origin_config)
                    if project_origin_config is not None else ROOT / 'laser-project-origins.json'))
    server = ThreadingHTTPServer((host,port),Handler)
    server.daemon_threads = False
    server.block_on_close = True
    try:
        server.data_root = Path(folder).resolve()
        server.inbox = inbox or Inbox(folder,code)
        server.surface = surface
        server.project_origins = origins
        return server
    except BaseException:
        server.server_close()
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=8766)
    parser.add_argument('--teacher-host',default='127.0.0.1')
    parser.add_argument('--teacher-port',type=int,default=8767)
    parser.add_argument('--data',type=Path,default=ROOT/'data'/'laser')
    args = parser.parse_args(argv)
    data = args.data or ROOT/'data'/'laser'
    assert_not_restored(data)
    data.mkdir(parents=True, exist_ok=True)
    lock = InstanceLock(data)
    server = teacher_server = None
    teacher_started = False
    try:
        assert_not_restored(data)
        write_protocol(data, 'laser')
        origins_config = data / 'laser-project-origins.json'
        server = create_server(data,args.host,args.port,project_origin_config=origins_config)
        teacher_server = create_server(data,args.teacher_host,args.teacher_port,surface='teacher',inbox=server.inbox,
                                       project_origin_config=origins_config)
        teacher_thread = threading.Thread(target=teacher_server.serve_forever,daemon=True)
        teacher_thread.start()
        teacher_started = True
        print(f'Laserværksted: http://{args.host}:{args.port}/',flush=True)
        print(f'Lærerside: http://{args.teacher_host}:{args.teacher_port}/',flush=True)
        print(f'Lærerkoden ligger i {data / "laererkode.txt"}',flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if server:
            server.server_close()
        if teacher_server:
            if teacher_started:
                teacher_server.shutdown()
                teacher_thread.join()
            teacher_server.server_close()
        lock.close()


if __name__ == '__main__':
    main()
