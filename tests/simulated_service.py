"""Subprocess fixture used only by verify_autostart.py; never bundled in the app."""
import json
import os
from pathlib import Path
import sys
import threading


def main():
    import app.server as server
    from app.paths import data_root

    folder = data_root() / 'school'
    machine_file = folder / 'simulated-printer.json'
    mutex = threading.Lock()

    class SimulatedPrinter:
        def __init__(self, config, queue, profiles):
            self.config, self.queue = config, queue

        def start(self):
            self.refresh()

        def stop(self):
            pass

        def refresh(self):
            machine = json.loads(machine_file.read_text())
            if not machine['network']:
                self.queue.offline(self.config['id'])
                return
            self.queue.update_report(self.config['id'], state=machine['state'], task=machine['task'],
                progress=machine.get('progress', 0), error=0,
                trays=[dict(slot=254, extruder=0, colour='#3678DC', material='PLA', profile='Bambu PLA Basic')])

        def upload(self, job, path):
            if not json.loads(machine_file.read_text())['network']:
                raise ConnectionError('Simulated network unavailable')

        def send(self, job):
            with mutex:
                machine = json.loads(machine_file.read_text())
                machine.update(state='RUNNING', task=job['remote_name'], progress=15)
                machine['sends'].append(job['id'])
                crash = machine.pop('crash_after_send', False)
                temporary = machine_file.with_suffix('.tmp')
                temporary.write_text(json.dumps(machine))
                temporary.replace(machine_file)
            if crash:
                os._exit(91)  # lost ACK, after the simulated physical start
            self.refresh()

    class SimulatedSlicer:
        def __init__(self, executable, profiles_root, work):
            self.executable = Path(executable)
            self.profiles = type('Profiles', (), {'items': {}})()

        def slice(self, *args):
            return folder / 'simulated.3mf'

    # Patch construction before Application exists: no physical network/camera code runs.
    server.BambuPrinter = SimulatedPrinter
    server.Slicer = SimulatedSlicer
    server.Cameras = lambda *_: None
    server.main()


if __name__ == '__main__':
    if '--supervisor' in sys.argv:
        from app.background import supervise
        from app.paths import data_root, service_port
        import subprocess
        code = 'import runpy; runpy.run_path(' + repr(str(Path(__file__).resolve())) + ', run_name="__main__")'
        command = [sys.executable, '-c', code, '--port', str(service_port()), '--host', '127.0.0.1']
        raise SystemExit(supervise(data_root() / 'school', service_port(), '127.0.0.1', server_command=command))
    main()
