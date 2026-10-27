import logging
import ftplib
import threading
import time
import subprocess
import hashlib
import json
import ssl
from pathlib import Path

from .job_diagnostics import save_diagnostic
from .core import TRANSFER_ATTEMPT_LIMIT
from .slicer import SliceCancelled

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, queue, slicer, drivers):
        self.queue, self.slicer, self.drivers = queue, slicer, drivers
        self.stop_event = threading.Event()
        self.threads = []
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.slice_lock = threading.Lock()
        # Reuse only files validated by this worker in this process. After a
        # restart Studio/profile versions cannot be proven unchanged: re-slice.
        self.prepared = {}

    def prepared_output(self, job, config):
        source = self.queue.folder / 'uploads' / f"{job['id']}.stl"
        key = hashlib.sha256(json.dumps({'config': config, 'digest': job['digest'],
            'quality': job['quality'], 'profile': job['profile'],
            'colour': job['actual_colour'], 'remote_name': job['remote_name']},
            sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        cached = self.prepared.pop(job['id'], None)
        if cached:
            previous_key, path, digest = cached
            if (previous_key == key and self.owned_output(job, path)
                    and hashlib.sha256(source.read_bytes()).hexdigest() == job['digest']
                    and hashlib.sha256(path.read_bytes()).hexdigest() == digest):
                self.prepared[job['id']] = cached
                return path
        output = self.slicer.slice(job, config, source)
        if isinstance(output, (str, Path)):
            path = Path(output)
            if self.owned_output(job, path):
                if len(self.prepared) >= 100:
                    self.prepared.pop(next(iter(self.prepared)))
                self.prepared[job['id']] = (key, path, hashlib.sha256(path.read_bytes()).hexdigest())
        return output

    def owned_output(self, job, path):
        root = self.queue.folder / 'sliced'
        directory = root / job['id']
        expected = directory / job['remote_name']
        return (path == expected and path.is_file()
                and not any(item.is_symlink() or getattr(item, 'is_junction', lambda: False)()
                            for item in (root, directory, path)))

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        cancel = getattr(self.slicer, 'cancel', None)
        if callable(cancel):
            cancel()
        self.thread.join()
        for thread in self.threads:
            thread.join()

    def run(self):
        while not self.stop_event.wait(1):
            try:
                self.queue.retry_pending_cleanup()
                for driver in list(self.drivers.values()):
                    try:
                        driver.refresh()
                    except Exception:
                        self.queue.offline(driver.config['id'])
                        log.exception('Printer refresh failed; will retry')
                self.threads = [t for t in self.threads if t.is_alive()]
                if len(self.threads) < max(1, len(self.drivers)):
                    job = self.queue.reserve()
                    if job:
                        thread = threading.Thread(target=self.process, args=(job,), daemon=True)
                        self.threads.append(thread)
                        thread.start()
            except Exception:
                log.exception('Queue scheduler failed')

    def process(self, job):
        uncertain = False
        stage = 'preparation'
        try:
            driver = self.drivers[job['printer_id']]
            if not self.queue.demo:
                with self.queue.lock:
                    current = self.queue.get_job(job['id'])
                    if current['status'] != 'preparing':
                        return
                    if current['transfer_attempts'] >= TRANSFER_ATTEMPT_LIMIT:
                        self.queue.begin_transfer(job['id'])
                        return
                with self.slice_lock:
                    if self.stop_event.is_set():
                        raise SliceCancelled('Lagskæring afbrudt ved lukning.')
                    stage = 'slicing'
                    config = {**self.queue.configs[job['printer_id']], 'extruder': job['extruder']}
                    output = self.prepared_output(job, config)
                stage = 'transfer'
                if self.stop_event.is_set():
                    raise SliceCancelled('Lagskæring afbrudt ved lukning.')
                if not self.queue.begin_transfer(job['id']):
                    return
                driver.upload(job, output)
                self.queue.transfer_succeeded(job['id'])
            else:
                time.sleep(2)
            stage = 'plate_check'
            # Re-check live colour, spool, state and cleared bed after the potentially slow upload.
            if self.stop_event.is_set() or not self.queue.begin_dispatch(job['id']):
                return
            uncertain = True
            stage = 'dispatch'
            driver.send(job)
            self.queue.dispatched(job['id'])
        except SliceCancelled:
            self.queue.retry_preparation(job['id'], 'Klargøring afbrudt ved lukning. Forsøges igen efter opstart.')
        except Exception as error:
            log.exception('Job %s failed (uncertain=%s)', job['id'], uncertain)
            try:
                save_diagnostic(self.queue.folder, job['id'], stage, error)
            except Exception:
                log.exception('Could not preserve preparation diagnostics for job %s', job['id'])
            message = str(error) if isinstance(error, ValueError) else 'Printstart er uafklaret. En voksen skal kontrollere printeren.' if uncertain else 'Printet kunne ikke klargøres eller overføres. En voksen skal kontrollere forbindelsen.'
            if stage == 'slicing' and isinstance(error, subprocess.TimeoutExpired):
                message = 'Bambu Studio kunne ikke klargøre modellen inden for 10 minutter. Se fejlrapporten på lærersiden.'
            if stage == 'transfer' and isinstance(error, (OSError, EOFError, ftplib.Error)):
                # FTPS and MQTT are separate connections. Only telemetry may
                # invalidate online/bed state; an upload never started motion.
                if isinstance(error, TimeoutError):
                    reason = 'tidsfristen blev overskredet'
                elif isinstance(error, ssl.SSLError):
                    reason = 'TLS-kontrollen fejlede'
                elif isinstance(error, ftplib.error_perm):
                    reason = 'printeren afviste filen'
                elif isinstance(error, ftplib.error_temp):
                    reason = 'printerens filoverførsel meldte en midlertidig fejl'
                else:
                    reason = 'forbindelsen til filoverførslen blev afbrudt'
                self.queue.retry_preparation(job['id'], f'Overførslen til printeren fejlede: {reason}. Se fejlrapporten på lærersiden.')
            else:
                self.queue.fail(job['id'], message, uncertain=uncertain)


class DemoPrinter:
    def __init__(self, config, queue, trays):
        self.config, self.queue, self.trays = config, queue, trays
        self.started = None
        self.job = None
        self.state = 'IDLE'

    def start(self):
        active = self.queue.active(self.config['id'])
        if active and active['status'] in ('printing', 'recovering', 'starting', 'dispatching'):
            # Resume only this simulated timer. No physical dispatch or network
            # reconnection exists in this driver; the durable job stays pinned.
            self.job = active
            self.started = time.time()
            self.state = 'RUNNING'
        self.refresh()
        if not active:
            self.queue.release_bed(self.config['id'])

    def stop(self):
        pass

    def refresh(self):
        elapsed = time.time() - self.started if self.started else 0
        progress = min(100, int(elapsed / 60 * 100))
        if self.started:
            self.state = 'FINISH' if progress == 100 else 'RUNNING'
        for tray in self.trays:
            tray['topology_seen'] = self.queue.clock()
        self.queue.update_report(self.config['id'], state=self.state, trays=self.trays, progress=progress, remaining=max(0, 1 - int(elapsed / 60)), error=0, task=self.job['remote_name'] if self.job else '')

    def send(self, job):
        self.job = job
        self.started = time.time()
        self.refresh()

    def finish(self):
        if not self.started or self.state != 'RUNNING':
            raise ValueError('Denne demoprinter er ikke i gang.')
        self.started = time.time() - 61
        self.refresh()


def demo_configs():
    return [
        {'id': 'demo-mini', 'name': 'A1 mini', 'model': 'A1 mini', 'enabled': True},
        {'id': 'demo-p1s', 'name': 'P1S', 'model': 'P1S', 'enabled': True},
        {'id': 'demo-h2d', 'name': 'H2D', 'model': 'H2D', 'enabled': True},
    ]


def demo_drivers(queue):
    colours = {'demo-mini': ['#3678DC'], 'demo-p1s': ['#E84142', '#F5D328', '#F4F4F4', '#20242A'], 'demo-h2d': ['#32AA69', '#925CCC', '#F28AB5', '#FF8B2C']}
    return {p['id']: DemoPrinter(p, queue, [{'slot': 254 if p['model'] == 'A1 mini' else (i + 2 if p['model'] == 'H2D' and i >= 2 else i), 'extruder': (1 if i < 2 else 0) if p['model'] == 'H2D' else 0, 'colour': colour, 'material': 'PLA', 'profile': 'Bambu PLA Basic', 'remaining': 80} for i, colour in enumerate(colours[p['id']])]) for p in queue.configs.values()}
