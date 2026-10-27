"""Exercise an isolated installed runtime. No reboot and no physical printers."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.process_tree import ProcessTree
from scripts.smoke_slice import cube


def eventually(action, timeout=35):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        try:
            last = action()
            if last:
                return last
        except (OSError, ValueError):
            pass
        time.sleep(.25)
    raise AssertionError(f'Condition did not become true: {last}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--install', type=Path, required=True)
    parser.add_argument('--task', required=True)
    args = parser.parse_args()
    install = args.install.resolve()
    assert install.is_relative_to(ROOT / '.runtime'), 'Only isolated workspace installations may be tested'
    config = json.loads((install / 'installation.json').read_text(encoding='utf-8-sig'))
    assert config['task_name'] == args.task and args.task.startswith('Printvaerksted-Isolated')
    assert config['port'] not in (8765, 8766)
    data = Path(config['data_root']) / 'school'
    assert data.is_relative_to(ROOT / '.runtime')
    python = str(install / 'runtime' / 'python.exe')
    base = f"http://127.0.0.1:{config['port']}"
    token = (data / 'laererkode.txt').read_text().strip()
    report = {'version': '0.4.3', 'physical_prints_sent': 0, 'windows_reboot_performed': False,
              'installation': str(install), 'checks': []}

    def request(path, body=None, headers=None):
        with urlopen(Request(base + path, data=body, headers=headers or {}), timeout=2) as response:
            return json.load(response)

    def post(path, value):
        return request(path, json.dumps(value).encode(), {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})

    def health():
        try:
            return request('/api/health')
        except OSError:
            return None

    def record(name):
        report['checks'].append(name)
        print('PASS: ' + name, flush=True)

    def desktop():
        return subprocess.run([python, '-m', 'app.desktop', '--no-window'], cwd=install,
                              capture_output=True, timeout=70, creationflags=subprocess.CREATE_NO_WINDOW)

    pid = eventually(health)['pid']
    assert request('/api/state')['paused']
    record('Installed task starts server without browser and restores pause')
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(result.returncode == 0 for result in pool.map(lambda _: desktop(), range(4)))
    assert health()['pid'] == pid
    record('Four simultaneous desktop launches reuse one server')
    os.kill(pid, signal.SIGTERM)
    pid = eventually(lambda: (h := health()) and h['pid'] != pid and h)['pid']
    record('Server crash is automatically recovered by supervisor')
    # Read only processes using this exact isolated interpreter.
    safe_python = python.replace("'", "''")
    ps = "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq '" + safe_python + "' -and $_.CommandLine -match '-m app.background' } | Select-Object -ExpandProperty ProcessId"
    result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', ps], capture_output=True, text=True, check=True)
    owners = [int(value) for value in result.stdout.split()]
    assert len(owners) == 1, owners
    os.kill(owners[0], signal.SIGTERM)
    eventually(lambda: not health(), timeout=10)
    pid = eventually(health, timeout=105)['pid']
    record('Task Scheduler restarts crashed supervisor and closes orphan server tree')
    post('/api/service/stop', {})
    eventually(lambda: not health())
    time.sleep(3)
    subprocess.run(['schtasks.exe', '/Run', '/TN', args.task + '-Recovery'], check=True, capture_output=True)
    time.sleep(3)
    assert not health()
    record('Intentional stop stays stopped even when the recovery task runs')

    settings_file = data / 'settings.json'
    settings = json.loads(settings_file.read_text())
    assert settings['printers'] == [], 'Do not replace existing printer settings'
    printer_id = 'a' * 32
    settings['printers'] = [dict(id=printer_id, name='Isolated simulator', model='A1 mini', enabled=True,
                                host='127.0.0.1', serial='SIMULATEDONLY', access_code='simulation', bed_type='Textured PEI Plate')]
    settings['retained_test_setting'] = 'preserve on upgrade'
    settings_file.write_text(json.dumps(settings))
    machine_file = data / 'simulated-printer.json'
    machine = dict(network=False, state='IDLE', task='', sends=[])

    def change_machine(**values):
        current = json.loads(machine_file.read_text()) if machine_file.exists() else machine
        current.update(values)
        tmp = machine_file.with_suffix('.control-tmp')
        tmp.write_text(json.dumps(current))
        tmp.replace(machine_file)

    def sends():
        return json.loads(machine_file.read_text())['sends']

    def jobs():
        return {j['id']: j for j in request('/api/state')['jobs']}

    def submit(number):
        return request('/api/jobs', cube(10), {'X-Filename': 'simulation.stl', 'X-Colour': 'any',
                       'X-Request-ID': f'isolated-restart-test-{number:04d}'})['id']

    def crash_server():
        old = health()['pid']
        os.kill(old, signal.SIGTERM)
        return eventually(lambda: (h := health()) and h['pid'] != old and h)['pid']

    change_machine()
    fixture = ROOT / 'tests' / 'simulated_service.py'
    code = 'import runpy; runpy.run_path(' + repr(str(fixture)) + ', run_name="__main__")'
    with (data / 'simulator.log').open('ab') as output:
        supervisor = subprocess.Popen([python, '-c', code, '--supervisor'], cwd=install, stdout=output,
                                      stderr=output, creationflags=subprocess.CREATE_NO_WINDOW)
    tree = ProcessTree(supervisor)
    try:
        eventually(health)
        first = submit(1)
        post('/api/queue/pause', {'paused': False})
        time.sleep(2)
        assert not sends() and jobs()[first]['status'] == 'queued'
        change_machine(network=True)
        eventually(lambda: request('/api/state')['printers'][0]['status'] == 'bed')
        assert not sends()
        post('/api/printers/release', {'id': printer_id})
        eventually(lambda: jobs()[first]['status'] == 'printing')
        assert sends() == [first]
        record('Offline startup waits, reconnects and starts only after fresh plate confirmation')
        crash_server()
        eventually(lambda: jobs()[first]['status'] == 'printing')
        assert sends() == [first]
        record('Restart during print finds same printer job without duplicate start')
        old = health()['pid']
        os.kill(old, signal.SIGTERM)
        change_machine(state='FINISH', progress=100)
        eventually(lambda: (h := health()) and h['pid'] != old)
        eventually(lambda: jobs()[first]['status'] == 'done')
        assert sends() == [first]
        record('Print completed while server was down is recovered as done')
        post('/api/queue/pause', {'paused': True})
        second = submit(2)
        crash_server()
        post('/api/printers/release', {'id': printer_id})
        time.sleep(2)
        assert request('/api/state')['paused'] and jobs()[second]['status'] == 'queued' and sends() == [first]
        record('Teacher pause survives process restart and cleared plate')
        # Crash at the narrowest safety boundary: printer started, no ACK recorded.
        change_machine(crash_after_send=True)
        old = health()['pid']
        post('/api/queue/pause', {'paused': False})
        eventually(lambda: (h := health()) and h['pid'] != old and jobs()[second]['status'] == 'printing')
        assert sends() == [first, second]
        record('Crash immediately after start request never sends the job twice')
        change_machine(state='IDLE', task='different-job')
        crash_server()
        eventually(lambda: jobs()[second]['status'] == 'attention')
        third = submit(3)
        time.sleep(2)
        assert jobs()[third]['status'] == 'queued' and sends() == [first, second]
        post('/api/queue/pause', {'paused': True})
        record('Ambiguous printer status holds job for teacher and blocks next print')
        post('/api/service/stop', {})
        supervisor.wait(timeout=20)
        assert supervisor.returncode == 0
    finally:
        tree.close()
        if supervisor.poll() is None:
            supervisor.kill()
        supervisor.wait(timeout=10)
        # Further installed task tests cannot target anything outside loopback.
        settings['printers'][0]['enabled'] = False
        settings_file.write_text(json.dumps(settings))

    report['simulated_start_commands'] = len(sends())
    report['simulated_distinct_jobs_started'] = len(set(sends()))
    report['reboot_before_login_still_pending'] = True
    output = ROOT / '.runtime' / 'autostart-verification-0.4.3.json'
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
