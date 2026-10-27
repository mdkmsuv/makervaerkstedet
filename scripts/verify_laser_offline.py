"""Exercise separate queue and station processes on loopback, never a physical laser."""
import argparse
import http.client
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.laser_service import ProcessTree


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def request(port, route, data=None):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=180)
    headers = {'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{port}'}
    connection.request('GET' if data is None else 'POST', route,
                       None if data is None else json.dumps(data), headers)
    response = connection.getresponse()
    raw = response.read(); connection.close()
    try: body = json.loads(raw)
    except ValueError: body = raw.decode()
    return response.status, body


def require(port, route, data=None):
    status, body = request(port, route, data)
    if status != 200:
        raise AssertionError(f'{route}: {status} {body}')
    return body


def wait_ready(port, route, process):
    for _ in range(60):
        if process.poll() is not None:
            raise RuntimeError('Local test child exited; inspect test logs.')
        try:
            if request(port, route)[0] == 200: return
        except (OSError, http.client.HTTPException): pass
        time.sleep(.2)
    raise TimeoutError('Local test server did not become ready.')


def verify(output, real_flux=False, flux_api=None):
    output.mkdir(parents=True, exist_ok=True)
    checks = []
    processes = []
    logs = []
    def launch(module, config, label):
        log = (output / (label + '.log')).open('wb'); logs.append(log)
        process = subprocess.Popen([sys.executable, '-m', module, '--config', str(config)], cwd=ROOT,
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        tree = ProcessTree(process); processes.append((process, tree))
        return process, tree
    try:
        with tempfile.TemporaryDirectory(prefix='laser-offline-') as temp:
            folder = Path(temp)
            queue_folder, plates = folder/'queue', folder/'station-plates'
            ports = set()
            while len(ports) < 3: ports.add(free_port())
            student_port, teacher_port, station_port = ports
            config = dict(data=str(queue_folder),host='127.0.0.1',port=student_port,
                          teacher_host='127.0.0.1',teacher_port=teacher_port)
            service_config = folder/'server.json'; service_config.write_text(json.dumps(config))
            process, tree = launch('app.laser_service', service_config, 'queue')
            wait_ready(student_port, '/api/laser/health', process)
            assert require(teacher_port, '/api/laser/health')['surface'] == 'teacher'
            checks.append('Separate student and teacher surfaces start through the service supervisor')
            assert request(student_port, '/api/laser/jobs')[0] == 404
            assert request(teacher_port, '/api/laser/jobs')[0] == 401
            checks.append('Student cannot list or download queue files; teacher login is required')
            svg = '<svg xmlns="http://www.w3.org/2000/svg" data-laservaerksted="1" width="80mm" height="60mm" viewBox="0 0 80 60"><g data-operation="engrave-line"><path data-operation="engrave-line" d="M20 20 H40"/></g><g data-operation="cut"><path data-operation="cut" d="M10 10 H60 V45 H10 Z"/></g></svg>'
            data = dict(elevnavn='Lokal test',klasse='TEST',projekt='Offline laserplade',requestId=uuid.uuid4().hex,svg=svg)
            first = require(student_port, '/api/laser/submit', data)
            duplicate = require(student_port, '/api/laser/submit', data)
            assert first['id'] == duplicate['id'] and duplicate['duplicate']
            checks.append('Submission persists once and retry returns the same job')
            # Stop only this test's owned queue tree and verify data recovery in a new process.
            tree.close(); process.wait(timeout=10)
            process, tree = launch('app.laser_service', service_config, 'queue-restarted')
            wait_ready(teacher_port, '/api/laser/health', process)
            station_config = folder/'station.json'
            station_data = dict(server=f'http://127.0.0.1:{teacher_port}',port=station_port,plates=str(plates))
            if not real_flux: station_data['flux_api'] = str(folder/'not-installed-flux.exe')
            elif flux_api: station_data['flux_api'] = str(flux_api)
            station_config.write_text(json.dumps(station_data))
            station, station_tree = launch('app.laser_station', station_config, 'station')
            wait_ready(station_port, '/api/station/state', station)
            code = (queue_folder/'laererkode.txt').read_text().strip()
            require(station_port, '/api/station/login', {'code': code})
            jobs = require(station_port, '/api/station/jobs')['jobs']
            assert [j['id'] for j in jobs] == [first['id']]
            checks.append('Station logs in and fetches persisted jobs over HTTP after queue restart')
            placement = require(station_port, '/api/station/place', {'ids':[first['id']]})
            plate = dict(material='wood_3mm',items=placement['placements'],open=False)
            result = require(station_port, '/api/station/plate/open', plate)
            assert result['opened'] is False and not result['problems']
            if real_flux:
                assert result['flux'] and result['flux']['seconds'] > 0, result
                checks.append('Real FLUX backend in simulation builds task and returns time estimate locally')
            produced = Path(result['file'])
            assert produced.is_relative_to(plates) and produced.is_file()
            assert not list(queue_folder.rglob('*.bvg'))
            assert not (plates/'afleveringer.sqlite3').exists()
            (output/'testplade.bvg').write_bytes(produced.read_bytes())
            checks.append('BVG production and files stay on station; canonical queue stays on server')
            station_tree.close(); station.wait(timeout=10)
            station, station_tree = launch('app.laser_station', station_config, 'station-restarted')
            wait_ready(station_port, '/api/station/state', station)
            state = require(station_port, '/api/station/state')
            assert state['lastPlate']['ids'] == [first['id']] and not state['loggedIn']
            require(station_port, '/api/station/login', {'code': code})
            require(station_port, '/api/station/plate/done', {'ids':[first['id']]})
            assert require(station_port, '/api/station/jobs')['jobs'] == []
            import sqlite3
            with sqlite3.connect(queue_folder/'afleveringer.sqlite3') as db:
                row = db.execute('SELECT status,payload_available,svg FROM jobs WHERE id=?',(first['id'],)).fetchone()
                assert row == ('done',0,None), row
            db.close()
            checks.append('Station restart restores plate; teacher completion updates server and clears payload')
            report = dict(passed=True,checks=checks,physical_laser_started=False,vm_contacted=False,
                          real_flux=real_flux,flux=result.get('flux'))
            # Close while TemporaryDirectory is alive so database/log handles do not block cleanup.
            for child, owned in reversed(processes):
                owned.close()
                if child.poll() is None: child.terminate()
                child.wait(timeout=10)
            # Job close is asynchronous for descendants, including the Windows
            # venv launcher. Wait for their file handles before temporary cleanup.
            import shutil
            assert folder.parent.resolve() == Path(tempfile.gettempdir()).resolve()
            assert folder.name.startswith('laser-offline-')
            for attempt in range(40):
                try:
                    shutil.rmtree(folder)
                    break
                except PermissionError:
                    if attempt == 39: raise
                    time.sleep(.25)
            (output/'verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            print(json.dumps(report,indent=2))
    finally:
        for child, owned in reversed(processes):
            owned.close()
            if child.poll() is None: child.terminate(); child.wait(timeout=10)
        for log in logs: log.close()


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--real-flux',action='store_true',help='Use installed FLUX backend only in simulation; never open Beam Studio')
    parser.add_argument('--flux-api', type=Path, help='Explicit FLUX backend path, with --real-flux')
    args=parser.parse_args()
    if args.flux_api and not args.real_flux:
        parser.error('--flux-api requires --real-flux')
    verify(args.output.resolve(),args.real_flux,args.flux_api)
