"""Keep explicitly selected camera lights on without restarting the print server."""
import argparse
import json
import logging
from pathlib import Path
import signal
import threading
import time

from .instance import InstanceLock
from .printer_lights import PrinterLight


def run(folder):
    folder = Path(folder)
    status_dir = folder / 'camera-lights'
    status_dir.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    for name in ('SIGINT', 'SIGTERM'):
        signal.signal(getattr(signal, name), lambda *_: stop.set())
    controllers = {}
    signatures = {}
    last_reload = None
    settings_error = None
    with InstanceLock(status_dir):
        try:
            while not stop.is_set():
                now = time.monotonic()
                if last_reload is None or now - last_reload >= 30:
                    last_reload = now
                    try:
                        policy = json.loads((status_dir / 'policy.json').read_text(encoding='utf-8-sig'))
                        settings = json.loads((folder / 'settings.json').read_text(encoding='utf-8-sig'))
                        allowed = set(policy['printer_ids']) if policy.get('always_on') is True else set()
                        configs = {p['id']: p for p in settings['printers']
                                   if p['id'] in allowed and p.get('model') == 'A1 mini'
                                   and all(p.get(k) for k in ('host', 'serial', 'access_code'))}
                        for pid in set(controllers) - configs.keys():
                            controllers.pop(pid).stop()
                            signatures.pop(pid, None)
                        for pid, config in configs.items():
                            signature = tuple(config[k] for k in ('host', 'serial', 'access_code'))
                            if signatures.get(pid) != signature:
                                if pid in controllers:
                                    controllers.pop(pid).stop()
                                controller = PrinterLight(config, folder / 'certificates')
                                controller.start()
                                controllers[pid] = controller
                                signatures[pid] = signature
                        settings_error = None
                    except Exception:
                        # Keep the last known desired state through a temporary
                        # settings write failure, without exposing private config.
                        settings_error = 'Lysopsætningen kunne ikke indlæses.'
                        logging.exception('Camera light configuration could not be loaded')
                reports = {}
                for pid, controller in controllers.items():
                    try:
                        controller.tick()
                        reports[pid] = controller.snapshot()
                    except Exception:
                        reports[pid] = {'confirmed_on': False, 'error': 'Lyskontrollen kunne ikke gennemføres.'}
                        logging.exception('Camera light control failed for %s', pid)
                status = {'sampled_at': time.time(), 'settings_error': settings_error, 'printers': reports}
                temp = status_dir / 'status.tmp'
                temp.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
                temp.replace(status_dir / 'status.json')
                stop.wait(5)
        finally:
            for controller in controllers.values():
                controller.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', required=True)
    args = parser.parse_args()
    folder = Path(args.data)
    log_dir = folder / 'camera-lights'
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=log_dir / 'service.log', level=logging.WARNING,
                        format='%(asctime)s %(levelname)s %(message)s')
    run(folder)


if __name__ == '__main__':
    main()
