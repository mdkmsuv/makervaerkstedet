"""Start the local service and open its UI from a Windows desktop shortcut."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen
from .paths import data_root, installation, service_port, queue_folder

ROOT = Path(__file__).resolve().parent.parent


def instance_id(folder):
    return hashlib.sha256(str(Path(folder).resolve()).casefold().encode()).hexdigest()[:24]


def health(url):
    try:
        with urlopen(url + '/api/health', timeout=1) as response:
            return json.load(response)
    except (URLError, OSError, ValueError):
        return None


def check_owner(status, folder, demo):
    if status and (status.get('service') != 'printvaerksted' or status.get('instance') != instance_id(folder) or status.get('demo') != demo):
        raise ValueError('En anden udgave af Printværksted bruger allerede adressen. Stop den anden udgave, og åbn genvejen igen.')


def ensure_service(folder, port, demo=False, local_only=False):
    url = f'http://127.0.0.1:{port}'
    status = health(url)
    check_owner(status, folder, demo)
    if status and status.get('ok'):
        return url
    installed = installation()
    if installed.get('task_name') and not demo:
        # The shortcut uses the same Windows task and service account as boot.
        result = subprocess.run(['schtasks.exe', '/Run', '/TN', installed['task_name']],
            capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise ValueError('Windows kunne ikke starte baggrundsopgaven. Kør installationen igen med administratorgodkendelse.')
        for _ in range(120):
            time.sleep(.5)
            status = health(url)
            check_owner(status, folder, demo)
            if status and status.get('ok'):
                return url
        raise ValueError(f'Baggrundsopgaven svarer ikke endnu. Se {folder / "background.log"}.')
    folder.mkdir(parents=True, exist_ok=True)
    python = Path(sys.executable).with_name('python.exe') if os.name == 'nt' else Path(sys.executable)
    command = [str(python), '-m', 'app.background', '--host', '127.0.0.1' if demo or local_only else '0.0.0.0', '--port', str(port), '--data', str(folder)]
    if demo:
        command.append('--demo')
    with (folder / 'startup.log').open('ab') as output:
        process = subprocess.Popen(command, cwd=ROOT, stdout=output, stderr=output, stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    for _ in range(60):
        time.sleep(0.5)
        status = health(url)
        check_owner(status, folder, demo)
        if status and status.get('ok'):
            return url
    raise ValueError(f'Printværksted kunne ikke starte. En voksen kan læse fejlen i {folder / "startup.log"}.')


def browser_command(url):
    for base, relative in (
        ('ProgramFiles(x86)', 'Microsoft/Edge/Application/msedge.exe'),
        ('ProgramFiles', 'Microsoft/Edge/Application/msedge.exe'),
        ('LOCALAPPDATA', 'Microsoft/Edge/Application/msedge.exe'),
        ('ProgramFiles', 'Google/Chrome/Application/chrome.exe'),
        ('LOCALAPPDATA', 'Google/Chrome/Application/chrome.exe'),
    ):
        if os.environ.get(base):
            browser = Path(os.environ[base]) / relative
            if browser.is_file():
                return [str(browser), f'--app={url}', '--no-first-run']
    raise ValueError('Installer Microsoft Edge eller Google Chrome for at åbne Printværksted som app.')


def stop_service(folder, port, demo, privileged=False):
    url = f'http://127.0.0.1:{port}'
    status = health(url)
    if not status:
        return
    check_owner(status, folder, demo)
    installed = installation()
    if not privileged and not demo and installed.get('task_name') and installed.get('autostart') == 'Boot':
        task_name = installed['task_name'] + '-Stop'
        result = subprocess.run(['schtasks.exe', '/Run', '/TN', task_name],
            capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise ValueError('Windows kunne ikke starte den beskyttede stopopgave. Kør installationen igen med administratorgodkendelse.')
        url = f'http://127.0.0.1:{port}'
        for _ in range(30):
            if not health(url):
                return
            time.sleep(0.2)
        raise ValueError('Programmet er ved at lukke. Vent et øjeblik, før du starter det igen.')
    token = (folder / 'laererkode.txt').read_text().strip()
    request = Request(url + '/api/service/stop', data=b'{}', headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
    with urlopen(request, timeout=5) as response:
        response.read()
    for _ in range(30):
        if not health(url):
            return
        time.sleep(0.2)
    raise ValueError('Programmet er ved at lukke. Vent et øjeblik, før du starter det igen.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--stop', action='store_true')
    parser.add_argument('--privileged-stop', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--check-stopped', action='store_true')
    parser.add_argument('--no-window', action='store_true', help='Service verification without opening the UI')
    parser.add_argument('--local-only', action='store_true')
    parser.add_argument('--port', type=int)
    args = parser.parse_args()
    try:
        if args.check_stopped:
            from .instance import InstanceLock
            locks = []
            try:
                for mode in ('school', 'demo'):
                    folder = queue_folder(mode == 'demo')
                    folder.mkdir(parents=True, exist_ok=True)
                    locks.append(InstanceLock(folder))
            finally:
                for lock in locks:
                    lock.close()
            return
        if args.stop or args.privileged_stop:
            modes = ((False, service_port()),) if args.privileged_stop else ((False, service_port()), (True, 8766))
            for mode, default_port in modes:
                stop_service(queue_folder(mode), args.port or default_port, mode, privileged=args.privileged_stop)
            return
        folder = queue_folder(args.demo)
        port = args.port or (8766 if args.demo else service_port())
        # Validate the browser before starting the queue.
        command = None if args.no_window else browser_command(f'http://127.0.0.1:{port}/')
        ensure_service(folder, port, args.demo, args.local_only)
        if command:
            subprocess.Popen(command, cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception as error:
        if args.no_window:
            raise
        ctypes.windll.user32.MessageBoxW(None, str(error), 'Printværksted', 0x10)
        sys.exit(1)


if __name__ == '__main__':
    main()
