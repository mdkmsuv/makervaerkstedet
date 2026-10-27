"""Laserstation: the teacher's simple surface at the Beambox II.

Runs on the makerspace computer next to the laser, only on 127.0.0.1. The queue stays
on the server (the Designværksted teacher surface, port 8767). The station places
deliveries on a plate, applies FLUX's presets for the chosen material, checks the task
with FLUX's own backend and opens the plate in Beam Studio. It never starts the laser.
"""
import argparse
from datetime import datetime
from functools import wraps
import hashlib
import http.client
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse, unquote, urlencode
import uuid

# Megavaerkstedet navigation v1
from app.megavaerkstedet import render as render_megavaerkstedet

from app import laser_plate as plate
from app import laser_material
from app.laser_ghost import FluxGhost, FluxGhostError
from app.laser_scene import MaterialScene, MAX_IMAGE_BYTES
from app.laser_transfer import FluxTransfer, TransferError
from app.instance import InstanceLock
from app.backup_guard import assert_not_restored, write_protocol
from app.laser_machines import get_machine

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / 'web'
DEFAULT_BEAM_STUDIO = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Beam Studio' / 'Beam Studio.exe'
STATIC = {
    '/megavaerkstedet.js': ('megavaerkstedet.js', 'text/javascript; charset=utf-8'),
    '/megavaerkstedet.css': ('megavaerkstedet.css', 'text/css; charset=utf-8'),
    '/': ('laserstation.html', 'text/html; charset=utf-8'),
    '/laserstation.js': ('laserstation.js', 'text/javascript; charset=utf-8'),
    '/laserstation.css': ('laserstation.css', 'text/css; charset=utf-8'),
    '/laser.css': ('laser.css', 'text/css; charset=utf-8'),
    '/favicon.svg': ('favicon.svg', 'image/svg+xml'),
    '/assets/metrophobic-latin.ttf': ('assets/metrophobic-latin.ttf', 'font/ttf'),
    '/assets/klostermarksskolen-logo.png': ('assets/klostermarksskolen-logo.png', 'image/png'),
}
MAX_BODY = 200_000
MAX_QUEUE_RESPONSE = 3_000_000
SUMMARY_PAGE_SIZE = 10
SUMMARY_MAX_PAGES = 1000
STATION_ID = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class NotLoggedIn(Exception):
    pass


class QueueUnavailable(Exception):
    pass


class QueueClient:
    """Talks to the Designværksted teacher surface with the teacher's session cookie."""

    def __init__(self, base_url, timeout=15, ca_file=None):
        parsed = urlparse(str(base_url))
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            raise ValueError('Køserveren skal være en http(s)-adresse.')
        if parsed.username or parsed.password or parsed.params or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
            raise ValueError('Køserveren skal være en ren adresse uden sti eller legitimationsoplysninger.')
        self.base_url = f'{parsed.scheme}://{parsed.netloc}'
        self.timeout = timeout
        # HTTPS with the school's own CA (the pilot uses one); None = system certificates.
        self.context = ssl.create_default_context(cafile=str(ca_file)) if ca_file else None
        self.cookie = None
        self.lock = threading.Lock()
        handlers = [urllib.request.ProxyHandler({}), _NoRedirect()]
        if self.context is not None:
            handlers.append(urllib.request.HTTPSHandler(context=self.context))
        self.opener = urllib.request.build_opener(*handlers)

    @property
    def logged_in(self):
        return self.cookie is not None

    def _request(self, route, data=None):
        body = json.dumps(data).encode('utf-8') if data is not None else None
        request = urllib.request.Request(self.base_url + route, data=body, method='POST' if body is not None else 'GET')
        if body is not None:
            request.add_header('Content-Type', 'application/json')
        if self.cookie:
            request.add_header('Cookie', self.cookie)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                payload = response.read(MAX_QUEUE_RESPONSE + 1)
                if len(payload) > MAX_QUEUE_RESPONSE:
                    raise QueueUnavailable('Køserverens svar er for stort.')
                return response.status, response.headers, payload
        except urllib.error.HTTPError as error:
            payload = error.read(MAX_QUEUE_RESPONSE + 1)
            error.close()
            if len(payload) > MAX_QUEUE_RESPONSE:
                raise QueueUnavailable('Køserverens fejlsvar er for stort.') from None
            if error.code == 401 and route != '/api/laser/login':
                self.cookie = None
                raise NotLoggedIn() from None
            try:
                message = json.loads(payload).get('error')
            except (ValueError, AttributeError):
                message = None
            raise ValueError(message or f'Serveren svarede {error.code}.') from None
        except (urllib.error.URLError, OSError) as error:
            raise QueueUnavailable(str(error)) from None
        except http.client.IncompleteRead:
            raise QueueUnavailable('Køserverens svar blev afbrudt.') from None

    def _json(self, route, data=None):
        payload = self._request(route, data)[2]
        try:
            return json.loads(payload)
        except (ValueError, TypeError, UnicodeDecodeError):
            raise QueueUnavailable('Køserveren sendte et ugyldigt svar.') from None

    def login(self, code):
        if not isinstance(code, str) or not 1 <= len(code) <= 200:
            raise ValueError('Skriv lærerkoden.')
        _, headers, _ = self._request('/api/laser/login', {'code': code})
        cookie = SimpleCookie(headers.get('Set-Cookie', ''))
        if 'laser_teacher' not in cookie:
            raise ValueError('Serveren gav ingen session.')
        with self.lock:
            self.cookie = f'laser_teacher={cookie["laser_teacher"].value}'

    def logout(self):
        if self.cookie:
            try:
                self._request('/api/laser/logout', {})
            except (ValueError, NotLoggedIn, QueueUnavailable):
                pass
        with self.lock:
            self.cookie = None

    def jobs(self, summary=False):
        if not self.cookie:
            raise NotLoggedIn()
        if not summary:
            payload = self._json('/api/laser/jobs')
            if not isinstance(payload, dict) or not isinstance(payload.get('jobs'), list):
                raise QueueUnavailable('Køserveren sendte et ugyldigt køsvar.')
            return payload['jobs']
        jobs, cursor, seen = [], None, set()
        for _ in range(SUMMARY_MAX_PAGES):
            query = [('summary', '1'), ('limit', str(SUMMARY_PAGE_SIZE))]
            if cursor is not None:
                query.append(('cursor', cursor))
            payload = self._json('/api/laser/jobs?' + urlencode(query))
            if (not isinstance(payload, dict) or not isinstance(payload.get('jobs'), list)
                    or len(payload['jobs']) > SUMMARY_PAGE_SIZE):
                raise QueueUnavailable('Køserveren sendte en ugyldig begrænset køside.')
            for job in payload['jobs']:
                if not isinstance(job, dict) or not isinstance(job.get('id'), str) or job['id'] in seen:
                    raise QueueUnavailable('Køserveren sendte dubletter i køsiderne.')
                seen.add(job['id'])
            jobs.extend(payload['jobs'])
            next_cursor = payload.get('nextCursor')
            if next_cursor is None:
                return jobs
            if (not isinstance(next_cursor, str) or not next_cursor or len(next_cursor) > 180
                    or next_cursor == cursor or len(payload['jobs']) == 0):
                raise QueueUnavailable('Køserveren sendte en ugyldig kømarkør.')
            cursor = next_cursor
        raise QueueUnavailable('Køen kunne ikke hentes inden for sidegrænsen.')

    def prepared_svg(self, job_id):
        if not self.cookie:
            raise NotLoggedIn()
        try:
            return self._request(f'/api/laser/jobs/{job_id}/svg')[2].decode('utf-8')
        except UnicodeDecodeError:
            raise QueueUnavailable('Køserveren sendte en ugyldig SVG-fil.') from None

    def image_mode(self, job_id, image_id, mode):
        return self._json(f'/api/laser/jobs/{job_id}/image-mode', {'imageId': image_id, 'mode': mode})

    def set_status(self, job_id, status):
        self._request(f'/api/laser/jobs/{job_id}/status', {'status': status})

    def station_configuration(self, station_id):
        result = self._json(f'/api/laser/stations/{station_id}/configuration')
        if not isinstance(result, dict) or 'station' not in result:
            raise QueueUnavailable('Køserveren gav ingen stationsopsætning.')
        station = result['station']
        if station is not None and (not isinstance(station, dict) or station.get('id') != station_id
                or station.get('model') not in ('fbb2', 'fhexa1') or type(station.get('enabled')) is not bool):
            raise QueueUnavailable('Køserveren gav en ugyldig stationsopsætning.')
        return station

    def check_handoff(self, station_id, model, ids):
        result = self._json(f'/api/laser/stations/{station_id}/handoff', {'model': model, 'ids': ids})
        if (not isinstance(result, dict) or type(result.get('managed')) is not bool
                or result.get('station_id') != station_id or result.get('ids') != ids):
            raise QueueUnavailable('Køserveren bekræftede ikke overdragelsen.')
        return result


def managed_operation(method):
    """Serialize station work and refresh central configuration before its use."""
    @wraps(method)
    def call(self, *args, **kwargs):
        with self.operation_lock:
            self.require_teacher()
            if self.operation_depth == 0:
                self.sync_configuration()
            self.operation_depth += 1
            try:
                return method(self, *args, **kwargs)
            finally:
                self.operation_depth -= 1
    return call


def job_summary(job):
    if 'details' not in job and 'images' in job:
        choices = job.get('images') or []
        operations = job.get('operations') or {}
        needs_choice = bool(job.get('needsChoice'))
    else:
        images = (job.get('details') or {}).get('images') or []
        modes = job.get('imageModes') or {}
        choices = [{'id': i['id'], 'name': i.get('name') or 'Billede', 'hasTrace': bool(i.get('hasTrace')), 'mode': modes.get(i['id'])}
                   for i in images if i.get('operation') != 'cut']
        operations = (job.get('details') or {}).get('operations') or {}
        needs_choice = any(c.get('mode') is None for c in choices)
    return {
        'id': job['id'], 'student': job['student'], 'classroom': job['classroom'], 'project': job['project'],
        'status': job['status'], 'created': job.get('created_at', job.get('created')), 'operations': operations,
        'images': choices, 'needsChoice': needs_choice,
    }


def entry_name(job):
    return f'{job["student"]} ({job["classroom"]}), {job["project"]}'


class Station:
    def __init__(self, queue, plates_dir, ghost=None, beam_studio=DEFAULT_BEAM_STUDIO, presets=None, opener=None,
                 transfer=None, machine='fbb2', station_id=None):
        self.queue = queue
        self.plates_dir = Path(plates_dir)
        self.ghost = ghost
        self.beam_studio = Path(beam_studio) if beam_studio else None
        self.machine = get_machine(machine)
        self.station_id = 'laser-' + self.machine['id'] if station_id is None else station_id
        if not isinstance(self.station_id, str) or not STATION_ID.fullmatch(self.station_id):
            raise ValueError('Stationens id skal være en lowercase slug på højst 40 tegn.')
        self.bed = (self.machine['width'], self.machine['height'])
        self.presets = presets or plate.load_presets(model=machine)
        if self.presets.get('model') != machine:
            raise ValueError('Materialeindstillingerne passer ikke til lasermodellen.')
        self.materials = plate.materials(self.presets)
        self.opener = opener or self._open_in_beam_studio
        self.geometry_cache = {}
        self.state_file = self.plates_dir / f'.laserstation-state-{hashlib.sha256(self.queue.base_url.encode("utf-8")).hexdigest()[:16]}.json'
        self.last_plate = self._load_last_plate()
        self.lock = threading.Lock()
        self.scene = MaterialScene(self.plates_dir / '.material-scenes', bed=self.bed)
        self.transfer = transfer if transfer is not None else FluxTransfer()
        self.transfer_lock = threading.Lock()
        self.prepared_transfer = None
        self.operation_lock = threading.RLock()
        self.operation_depth = 0
        self.managed_station = None

    def sync_configuration(self):
        # Real QueueClient instances always fetch this endpoint. Lightweight
        # offline geometry fixtures can omit it; network errors never fall back.
        loader = getattr(self.queue, 'station_configuration', None)
        if loader is None:
            return
        config = loader(self.station_id)
        if config is None:
            if self.managed_station is not None:
                raise QueueUnavailable('Stationen findes ikke længere i den centrale opsætning.')
            return
        if config['model'] != self.machine['id']:
            with self.lock:
                last = self.last_plate
            if last:
                active_ids = {job['id'] for job in self._jobs() if job['status'] != 'done'}
                if any(job_id in active_ids for job_id in last['ids']):
                    raise ValueError('Afslut den seneste plade, før stationens lasermodel ændres.')
                with self.lock:
                    self.last_plate = None
                    self._save_last_plate()
            profile = get_machine(config['model'])
            presets = plate.load_presets(model=profile['id'])
            self.machine, self.presets = profile, presets
            self.bed = (profile['width'], profile['height'])
            self.materials = plate.materials(presets)
            # Old approvals and compiled files cannot carry over to another
            # work area. Retain files on disk; only invalidate in-memory state.
            self.scene = MaterialScene(self.plates_dir / '.material-scenes', bed=self.bed)
            self.prepared_transfer = None
            self.geometry_cache.clear()
        self.managed_station = config

    def require_handoff(self, entries):
        check = getattr(self.queue, 'check_handoff', None)
        if check is not None:
            result = check(self.station_id, self.machine['id'], [entry['id'] for entry in entries])
            if self.managed_station is not None and result.get('managed') is not True:
                raise QueueUnavailable('Stationens aktuelle workflow kunne ikke bekræftes.')

    def require_teacher(self):
        if not self.queue.logged_in:
            raise NotLoggedIn()

    def _jobs(self):
        """Fetch the bounded station summary from the queue API."""
        return self.queue.jobs(summary=True)

    @managed_operation
    def image_mode(self, job_id, image_id, mode):
        result = self.queue.image_mode(job_id, image_id, mode)
        self.geometry_cache.clear()
        self.prepared_transfer = None
        return result

    @managed_operation
    def material_image(self, data):
        self.require_teacher()
        return {'scene': self.scene.new_image(data)}

    def material_photo(self, scene_id):
        self.require_teacher()
        return self.scene.image(scene_id)

    @managed_operation
    def material_regions(self, data):
        self.require_teacher()
        return {'scene': self.scene.update_regions(data)}

    @managed_operation
    def material_approve(self, data):
        self.require_teacher()
        return {'scene': self.scene.approve_regions(data)}

    def material_clear(self):
        self.require_teacher()
        self.scene.clear()
        return {'scene': None}

    def _load_last_plate(self):
        try:
            data = json.loads(self.state_file.read_text(encoding='utf-8'))
            last = data.get('lastPlate')
            if not isinstance(last, dict) or not isinstance(last.get('ids'), list) or not last['ids']:
                return None
            return last
        except (OSError, ValueError, TypeError):
            return None

    def _save_last_plate(self):
        self.plates_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.state_file.with_name(self.state_file.name + f'.{uuid.uuid4().hex}.tmp')
        temporary.write_text(json.dumps({'lastPlate': self.last_plate}, ensure_ascii=False), encoding='utf-8')
        temporary.replace(self.state_file)

    # --- queue ------------------------------------------------------------------------
    @managed_operation
    def jobs(self):
        return [job_summary(j) for j in self._jobs()
                if j['status'] != 'done' and j.get('payload_available', True)]

    @managed_operation
    def geometry(self, job_id):
        svg = self.queue.prepared_svg(job_id)
        key = hashlib.sha256(svg.encode('utf-8')).hexdigest()
        with self.lock:
            cached = self.geometry_cache.get(job_id)
            if cached and cached[0] == key:
                return cached[1]
        geometry = plate.job_geometry(svg)
        with self.lock:
            if len(self.geometry_cache) > 300:
                self.geometry_cache.clear()
            self.geometry_cache[job_id] = (key, geometry)
        return geometry

    def entries(self, data):
        jobs = {j['id']: j for j in self._jobs()
                if j['status'] != 'done' and j.get('payload_available', True)}
        placements = plate.read_placements(data, set(jobs))
        return [dict(p, name=entry_name(jobs[p['id']]), geometry=self.geometry(p['id'])) for p in placements]

    # --- plate --------------------------------------------------------------------------
    @managed_operation
    def auto_place(self, data):
        scene = self.scene.require(data, approved=True)
        ids = data.get('ids')
        if not isinstance(ids, list) or not 1 <= len(ids) <= plate.MAX_ITEMS or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
            raise ValueError('Vælg de afleveringer, der skal på pladen.')
        known = {j['id'] for j in self._jobs()
                 if j['status'] != 'done' and j.get('payload_available', True)}
        if any(not isinstance(i, str) or i not in known for i in ids):
            raise ValueError('En aflevering findes ikke længere i køen.')
        items = [{'id': i, 'width': g['width'], 'height': g['height']} for i, g in ((i, self.geometry(i)) for i in ids)]
        if scene:
            placements, unplaced = laser_material.place_on_material(items, scene['regions'], margin=scene['margin'], bed=self.bed)
        else:
            placements, unplaced = plate.auto_place(items, bed=self.bed)
        self.scene.require(data, approved=True)
        return {'placements': placements, 'unplaced': unplaced}

    @managed_operation
    def check(self, data, need_flux=False, run_flux=True):
        scene = self.scene.require(data, approved=True)
        material = plate.material_by_id(self.presets, data.get('material'))
        entries = self.entries(data)
        problems = plate.check_plate(entries, bed=self.bed)
        if scene:
            problems.extend(laser_material.check_material(entries, scene['regions'], margin=scene['margin'], bed=self.bed))
        result = {'problems': problems, 'extent': [round(v, 1) for v in plate.expected_extent(entries)], 'flux': None}
        if problems:
            return result, None, material, entries
        document = plate.build_bvg(entries, material, model=self.machine['id'], bed=self.bed)
        if not run_flux:
            return result, document, material, entries
        if self.ghost and self.ghost.available():
            try:
                task = self.ghost.task(document, model=self.machine['id'])
                meta = task['metadata']
                result['flux'] = {'seconds': round(task['seconds'], 1), 'bytes': task['bytes'],
                                  'extent': [float(meta[k]) for k in ('min_x', 'min_y', 'max_x', 'max_y') if k in meta]}
            except FluxGhostError as error:
                result['fluxError'] = str(error)
                if need_flux:
                    raise ValueError(str(error)) from None
        else:
            result['fluxError'] = 'FLUX-programmet fra Beam Studio blev ikke fundet, så tiden kan ikke beregnes.'
        self.scene.require(data, approved=True)
        return result, document, material, entries

    def approval_fingerprint(self, data, material, entries):
        scene = self.scene.require(data, approved=True)
        value = {'scene': scene, 'machine': self.machine['id'], 'material': material, 'entries': entries}
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                         allow_nan=False).encode('utf-8')).hexdigest()

    @managed_operation
    def approve_plate(self, data):
        result, _, material, entries = self.check(data, run_flux=False)
        if result['problems']:
            raise ValueError(' '.join(result['problems']))
        with self.scene.lock:
            fingerprint = self.approval_fingerprint(data, material, entries)
            token = self.scene.approve_placements(data, fingerprint)
        return {'approvalToken': token, 'problems': []}

    @managed_operation
    def prepare_transfer(self, data):
        """Compile locally, without opening Beam Studio or contacting a laser."""
        if not self.transfer_lock.acquire(blocking=False):
            raise ValueError('En laserfil er allerede ved at blive klargjort eller sendt.')
        try:
            result, document, material, entries = self.check(data, run_flux=False)
            if result['problems']:
                raise ValueError(' '.join(result['problems']))
            with self.scene.lock:
                fingerprint = self.approval_fingerprint(data, material, entries)
                self.scene.validate_approval(data, fingerprint, mandatory=True)
            self.require_handoff(entries)
            if not self.ghost or not self.ghost.available():
                raise ValueError('FLUX-programmet skal installeres, før en laserfil kan klargøres.')
            try:
                task = self.ghost.task(document, model=self.machine['id'])
            except FluxGhostError as error:
                raise ValueError(str(error)) from None
            content = task.get('fcode')
            if not isinstance(content, bytes) or not 1 <= len(content) <= 64 * 1024 * 1024:
                raise ValueError('FLUX gav ikke en brugbar laserfil.')
            with self.scene.lock:
                scene = self.scene.validate_approval(data, fingerprint, mandatory=True)
                transfer_id = uuid.uuid4().hex
                self.plates_dir.mkdir(parents=True, exist_ok=True)
                file = self.plates_dir / f'laserfil-{transfer_id}.fc'
                file.write_bytes(content)
                record = {'id': transfer_id, 'created': datetime.now().isoformat(timespec='seconds'),
                          'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content),
                          'seconds': task.get('seconds'), 'material': material['id'], 'machine': self.machine['id'],
                          'items': [{k: e[k] for k in ('id', 'name', 'x', 'y', 'rotation')} for e in entries],
                          'scene': scene, 'teacherApproved': True, 'sent': False, 'started': False}
                file.with_suffix('.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
                self.prepared_transfer = {'id': transfer_id, 'file': file, 'record': record,
                                          'fingerprint': fingerprint, 'expires': time.monotonic() + 900,
                                          'attempted': False}
            return {'transferId': transfer_id, 'bytes': len(content), 'seconds': task.get('seconds'),
                    'sent': False, 'started': False, 'directTransfer': self.transfer.status()}
        finally:
            self.transfer_lock.release()

    @managed_operation
    def send_transfer(self, data):
        if data.get('confirmed') is not True:
            raise ValueError('Bekræft overførslen til laseren.')
        if not self.transfer_lock.acquire(blocking=False):
            raise ValueError('En laserfil er allerede ved at blive klargjort eller sendt.')
        try:
            result, _, material, entries = self.check(data, run_flux=False)
            if result['problems']:
                raise ValueError(' '.join(result['problems']))
            with self.scene.lock:
                fingerprint = self.approval_fingerprint(data, material, entries)
                self.scene.validate_approval(data, fingerprint, mandatory=True)
                prepared = self.prepared_transfer
                if (not prepared or data.get('transferId') != prepared['id'] or prepared['attempted']
                        or time.monotonic() >= prepared['expires'] or prepared['fingerprint'] != fingerprint):
                    raise ValueError('Godkend og klargør den aktuelle plade igen før afsendelse.')
                if not self.transfer.status()['available']:
                    raise ValueError(self.transfer.status()['message'])
                content = prepared['file'].read_bytes()
                if hashlib.sha256(content).hexdigest() != prepared['record']['sha256']:
                    raise ValueError('Laserfilen er ændret. Klargør den igen.')
                self.require_handoff(entries)
                # Never retry automatically: a timeout may leave a complete file on the machine.
                prepared['attempted'] = True
                receipt = self.transfer.upload(content, job_id=prepared['id'], model=self.machine['id'])
                if (not isinstance(receipt, dict) or receipt.get('verified') is not True
                        or receipt.get('started') is not False):
                    raise TransferError('Laseren gav ikke en gyldig kvittering. Kontrollér maskinen før et nyt forsøg.')
                prepared['record'].update(sent=True, receipt=receipt)
                prepared['file'].with_suffix('.json').write_text(
                    json.dumps(prepared['record'], ensure_ascii=False, indent=2), encoding='utf-8')
                with self.lock:
                    self.last_plate = {'file': str(prepared['file']), 'ids': [e['id'] for e in entries],
                                       'material': material['name'], 'names': [e['name'] for e in entries],
                                       'created': prepared['record']['created'], 'method': 'direct-transfer',
                                       'physicalStarted': False}
                    self._save_last_plate()
            return {'sent': True, 'started': False, 'receipt': receipt}
        finally:
            self.transfer_lock.release()

    @managed_operation
    def open_plate(self, data):
        result, document, material, entries = self.check(data)
        if result['problems']:
            raise ValueError(' '.join(result['problems']))
        # A photograph/region edit and export cannot race past the approval gate.
        with self.scene.lock:
            fingerprint = self.approval_fingerprint(data, material, entries)
            scene = self.scene.validate_approval(data, fingerprint)
            self.require_handoff(entries)
            return self._save_and_open(data, result, document, material, entries, scene)

    def _save_and_open(self, data, result, document, material, entries, scene):
        self.plates_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime('%Y-%m-%d-%H%M%S-%f')
        base = self.plates_dir / f'laserplade-{stamp}-{uuid.uuid4().hex[:8]}-{material["id"]}'
        bvg_file = base.with_suffix('.bvg')
        bvg_file.write_text(document, encoding='utf-8')
        record = {
            'created': datetime.now().isoformat(timespec='seconds'), 'material': material['id'], 'materialName': material['name'],
            'machine': self.machine['id'],
            'presets': {'cut': material['cutPreset'], 'engrave': material['engravePreset']}, 'flux': result.get('flux'),
            'items': [{k: e[k] for k in ('id', 'name', 'x', 'y', 'rotation')} for e in entries],
        }
        if scene:
            record['materialScene'] = {k: scene[k] for k in ('id', 'revision', 'imageSha256', 'heightMm',
                                                          'corners', 'regions', 'margin')}
            record['teacherApproved'] = True
        base.with_suffix('.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        opened, open_error = False, None
        if data.get('open', True):
            try:
                self.opener(bvg_file)
                opened = True
            except (OSError, ValueError) as error:
                open_error = str(error)
        with self.lock:
            self.last_plate = {'file': str(bvg_file), 'ids': [e['id'] for e in entries], 'material': material['name'],
                               'names': [e['name'] for e in entries], 'created': record['created']}
            self._save_last_plate()
        return dict(result, file=str(bvg_file), opened=opened, openError=open_error)

    def _open_in_beam_studio(self, path):
        if not self.beam_studio or not self.beam_studio.is_file():
            raise ValueError('Beam Studio blev ikke fundet. Filen er gemt, så den kan åbnes manuelt.')
        flags = getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
        subprocess.Popen([str(self.beam_studio), str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)

    def mark_done(self, data):
        self.require_teacher()
        ids = data.get('ids')
        with self.lock:
            last = self.last_plate
        if not isinstance(ids, list) or not ids or not last or any(i not in last['ids'] for i in ids):
            raise ValueError('Markér kun afleveringer fra den seneste plade.')
        marked = 0
        failures = []
        for job_id in ids:
            try:
                self.queue.set_status(job_id, 'done')
                marked += 1
                with self.lock:
                    if self.last_plate and job_id in self.last_plate['ids']:
                        current_ids = self.last_plate['ids']
                        current_names = self.last_plate.get('names', [])
                        # Older partially completed plates may have stale names.
                        # Never attach a different pupil's name to a retry ID.
                        if len(current_names) != len(current_ids):
                            current_names = [f'Aflevering {i[:8].upper()}' for i in current_ids]
                        remaining = [(i, name) for i, name in zip(current_ids, current_names) if i != job_id]
                        self.last_plate = dict(self.last_plate, ids=[i for i, _ in remaining],
                                               names=[name for _, name in remaining]) if remaining else None
                        self._save_last_plate()
            except (QueueUnavailable, NotLoggedIn, ValueError):
                failures.append(job_id)
        if failures:
            raise QueueUnavailable(f'{marked} aflevering(er) blev markeret; kunne ikke markere {len(failures)}.')
        return {'marked': marked}

    def state(self):
        with self.lock:
            last = dict(self.last_plate) if self.last_plate else None
        return {
            'loggedIn': self.queue.logged_in, 'server': self.queue.base_url, 'stationId': self.station_id,
            'beamStudio': bool(self.beam_studio and self.beam_studio.is_file()),
            'flux': bool(self.ghost and self.ghost.available()),
            'bed': {'width': self.bed[0], 'height': self.bed[1], 'machine': self.machine['name'],
                    'model': self.machine['id'], 'cameraWidth': self.machine['cameraWidth'],
                    'cameraHeight': self.machine['cameraHeight']},
            'presetSource': self.presets.get('source'), 'materials': self.materials, 'lastPlate': last,
            'materialScene': self.scene.snapshot(),
            'directTransfer': self.transfer.status(),
            'managedStation': self.managed_station,
        }

    def overview(self):
        """Minimal, read-only identity snapshot for the makerspace server.

        This endpoint deliberately avoids queue, material, student, scene and
        physical-device probing.  ``status`` and ``online`` remain unknown/
        false until a separately approved physical probe exists.
        """
        return {
            'status': 'ok', 'checked_at': time.time(),
            'items': [{
                'id': self.station_id,
                'model': self.machine['id'],
                'name': (getattr(self, 'managed_station', None) or {}).get('name', self.machine['name']),
                'software': {
                    'beam_studio_configured': bool(self.beam_studio),
                    'flux_configured': bool(self.ghost and getattr(self.ghost, 'executable', None)),
                },
                'status': 'unknown', 'online': False,
            }],
        }


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        # Browser preconnects and incomplete requests must not hold graceful
        # shutdown forever. This limits socket I/O, not laser/geometry work.
        self.connection.settimeout(3)

    server_version = 'Laserstation'

    @property
    def station(self):
        return self.server.station

    def log_message(self, *args):
        pass

    def reply(self, status, body, mime='application/json; charset=utf-8', policy=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', policy or "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def allowed_host(self):
        # Require both a loopback peer and one unambiguous loopback Host value.
        port = self.server.server_port
        try:
            peer_loopback = ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            peer_loopback = False
        hosts = self.headers.get_all('Host') or []
        return peer_loopback and len(hosts) == 1 and hosts[0] in (f'127.0.0.1:{port}', f'localhost:{port}')

    def guarded(self, action, mime='application/json; charset=utf-8'):
        try:
            return self.reply(200, action(), mime)
        except NotLoggedIn:
            return self.reply(401, {'error': 'Log ind med lærerkoden til laserkøen.'})
        except QueueUnavailable:
            return self.reply(502, {'error': 'Serveren med afleveringskøen svarer ikke. Tjek netværket, og prøv igen.'})
        except ValueError as error:
            return self.reply(400, {'error': str(error)})
        except OSError:
            return self.reply(500, {'error': 'Laserstationen kunne ikke læse eller gemme filen. Tjek plads og adgang til mappen.'})

    def do_GET(self):
        if not self.allowed_host():
            return self.reply(403, {'error': 'Åbn laserstationen på http://127.0.0.1.'})
        route = unquote(urlparse(self.path).path)
        if route == '/megavaerkstedet.html':
            page, policy = render_megavaerkstedet(WEB)
            return self.reply(200, page, 'text/html; charset=utf-8', policy=policy)
        if route in STATIC:
            name, mime = STATIC[route]
            file = WEB / name
            if file.is_file():
                return self.reply(200, file.read_bytes(), mime)
            return self.reply(404, {'error': 'Siden findes ikke.'})
        if route == '/api/station/state':
            return self.reply(200, self.station.state())
        if route == '/api/station/overview':
            return self.reply(200, self.station.overview())
        if route == '/api/station/jobs':
            return self.guarded(lambda: {'jobs': self.station.jobs()})
        match = re.fullmatch(r'/api/station/material/image/([a-f0-9]{32})', route)
        if match:
            return self.guarded(lambda: self.station.material_photo(match[1]), 'image/png')
        match = re.fullmatch(r'/api/station/jobs/([a-f0-9]{32})/geometry', route)
        if match:
            return self.guarded(lambda: self.station.geometry(match[1]))
        self.reply(404, {'error': 'Siden findes ikke.'})

    def do_POST(self):
        port = self.server.server_port
        if not self.allowed_host() or self.headers.get('Origin') not in (f'http://127.0.0.1:{port}', f'http://localhost:{port}'):
            return self.reply(403, {'error': 'Brug laserstationens egen side.'})
        if self.headers.get_content_type() != 'application/json':
            return self.reply(415, {'error': 'Brug laserstationens egen side.'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            length = -1
        route = urlparse(self.path).path
        maximum = MAX_IMAGE_BYTES * 4 // 3 + MAX_BODY if route == '/api/station/material/image' else MAX_BODY
        if not 0 < length <= maximum:
            return self.reply(413, {'error': 'Forespørgslen er for stor.'})
        try:
            data = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            return self.reply(400, {'error': 'Ugyldig forespørgsel.'})
        if not isinstance(data, dict):
            return self.reply(400, {'error': 'Ugyldig forespørgsel.'})
        station = self.station
        if route == '/api/station/login':
            return self.guarded(lambda: (station.queue.login(data.get('code')), {'ok': True})[1])
        if route == '/api/station/logout':
            station.queue.logout()
            station.scene.clear()
            return self.reply(200, {'ok': True})
        match = re.fullmatch(r'/api/station/jobs/([a-f0-9]{32})/image-mode', route)
        if match:
            return self.guarded(lambda: station.image_mode(match[1], data.get('imageId'), data.get('mode')))
        actions = {
            '/api/station/place': lambda: station.auto_place(data),
            '/api/station/plate/check': lambda: station.check(data)[0],
            '/api/station/plate/approve': lambda: station.approve_plate(data),
            '/api/station/transfer/prepare': lambda: station.prepare_transfer(data),
            '/api/station/transfer/send': lambda: station.send_transfer(data),
            '/api/station/plate/open': lambda: station.open_plate(data),
            '/api/station/plate/done': lambda: station.mark_done(data),
            '/api/station/material/image': lambda: station.material_image(data),
            '/api/station/material/regions': lambda: station.material_regions(data),
            '/api/station/material/approve': lambda: station.material_approve(data),
            '/api/station/material/clear': station.material_clear,
        }
        if route in actions:
            return self.guarded(actions[route])
        self.reply(404, {'error': 'Funktionen findes ikke.'})


def create_station_server(station, port=8790):
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = False
    server.block_on_close = True
    server.station = station
    return server


def default_plates_dir():
    documents = Path.home() / 'Documents'
    return (documents if documents.is_dir() else Path.home()) / 'Laserplader'


def main(argv=None, stop_event=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--config', type=Path, default=None, help='JSON-konfiguration for stationen')
    parser.add_argument('--server', default=None, help='Designværkstedets lærerside med laserkøen')
    parser.add_argument('--port', type=int, default=None)
    parser.add_argument('--plates', type=Path, default=None, help='Mappe til gemte laserplader')
    parser.add_argument('--beam-studio', type=Path, default=None)
    parser.add_argument('--flux-api', type=Path, default=None, help='FLUX-programmet fra Beam Studio (flux_api.exe)')
    parser.add_argument('--ca-file', type=Path, default=None, help='Skolens CA-certifikat, når køen nås over HTTPS')
    args = parser.parse_args(argv)
    config = {}
    if args.config:
        try:
            raw_config = args.config.read_text(encoding='utf-8')
            if len(raw_config) > 64 * 1024:
                raise ValueError('Konfigurationsfilen er for stor.')
            config = json.loads(raw_config)
            if not isinstance(config, dict):
                raise ValueError('Konfigurationen skal være et JSON-objekt.')
        except (OSError, ValueError, UnicodeDecodeError) as error:
            parser.error(f'Kunne ikke læse konfigurationen: {error}')
    server_url = args.server or config.get('server') or 'http://127.0.0.1:8767'
    port = args.port if args.port is not None else config.get('port', 8790)
    configured_plates = config.get('plates')
    plates = args.plates or (Path(configured_plates) if configured_plates else default_plates_dir())
    beam_studio = args.beam_studio or (Path(config['beam_studio']) if config.get('beam_studio') else DEFAULT_BEAM_STUDIO)
    flux_api = args.flux_api or (Path(config['flux_api']) if config.get('flux_api') else None)
    ca_file = args.ca_file or (Path(config['ca_file']) if config.get('ca_file') else None)
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        parser.error('Portnummeret skal være mellem 1 og 65535.')
    assert_not_restored(plates)
    plates.mkdir(parents=True, exist_ok=True)
    lock = InstanceLock(plates)
    server = ghost = None
    stop_watcher = None
    watcher_finished = threading.Event()
    try:
        assert_not_restored(plates)
        write_protocol(plates, 'laser_station')
        ghost = FluxGhost(flux_api, workdir=Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'Laserstation' / 'flux')
        station = Station(QueueClient(server_url, ca_file=ca_file), plates, ghost=ghost, beam_studio=beam_studio,
                          transfer=FluxTransfer(config.get('direct_transfer')),
                          machine=config.get('machine', 'fbb2'), station_id=config.get('station_id'))
        server = create_station_server(station, port)
        print(f'Laserstation: http://127.0.0.1:{port}/', flush=True)
        print(f'Afleveringskø: {server_url}', flush=True)
        print(f'Laserplader gemmes i {plates}', flush=True)
        if stop_event is not None:
            def request_shutdown():
                while not watcher_finished.wait(0.1):
                    if stop_event.is_set():
                        server.shutdown()
                        return
            stop_watcher = threading.Thread(target=request_shutdown, name='laser-station-stop', daemon=True)
            stop_watcher.start()
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        watcher_finished.set()
        if server:
            server.server_close()
        try:
            if ghost: ghost.stop()
        finally:
            lock.close()
        if stop_watcher:
            stop_watcher.join(timeout=2)


if __name__ == '__main__':
    main()
