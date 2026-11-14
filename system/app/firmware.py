"""Weekly, read-only A1 mini release checks. Never contacts or updates a printer."""
from datetime import date
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlparse
from urllib.request import Request, urlopen

HISTORY_URL = 'https://wiki.bambulab.com/en/a1-mini/manual/a1-mini-firmware-release-history'
DOWNLOAD_URL = 'https://bambulab.com/en/support/firmware-download/a1-mini'
INTERVAL = 7 * 24 * 60 * 60
MAX_PAGE = 2 * 1024 * 1024
VERSION = re.compile(r'(?<!\d)(\d{2}\.\d{2}\.\d{2}\.\d{2})(?!\d)')


class ReleaseHeadings(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tag = None
        self.text = []
        self.headings = []

    def handle_starttag(self, tag, attrs):
        if tag in ('h1', 'h2', 'h3'):
            self.tag, self.text = tag, []

    def handle_data(self, data):
        if self.tag:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag == self.tag:
            self.headings.append(''.join(self.text))
            self.tag = None


def parse_release(html):
    parser = ReleaseHeadings()
    parser.feed(html)
    releases = []
    for heading in parser.headings:
        if not re.search(r'\b(?:version|verison)\b', heading, re.I):
            continue
        if re.search(r'\b(?:beta|alpha|preview|rc)\b', heading, re.I):
            continue
        match = VERSION.search(heading)
        if not match:
            continue
        published = None
        stamp = re.search(r'\b(20\d{2})[./-]?(\d{2})[./-]?(\d{2})\b', heading[match.end():])
        if stamp:
            try:
                published = date(*map(int, stamp.groups())).isoformat()
            except ValueError:
                pass
        releases.append({'version': match[1], 'published_on': published})
    if not releases:
        raise ValueError('No release headings found')
    return max(releases, key=lambda row: tuple(map(int, row['version'].split('.'))))


def fetch_release():
    request = Request(HISTORY_URL, headers={'User-Agent': 'Printvaerksted/0.4.7 firmware-check', 'Accept': 'text/html'})
    with urlopen(request, timeout=10) as response:
        final = urlparse(response.url)
        if final.scheme != 'https' or final.hostname != 'wiki.bambulab.com':
            raise ValueError('Unexpected release source')
        body = response.read(MAX_PAGE + 1)
        if len(body) > MAX_PAGE:
            raise ValueError('Release page too large')
    return parse_release(body.decode('utf-8'))


class FirmwareChecks:
    def __init__(self, folder, enabled=True, clock=time.time, fetch=fetch_release):
        self.path = Path(folder) / 'firmware-check.json'
        self.enabled, self.clock, self.fetch = enabled, clock, fetch
        self.lock = threading.RLock()
        self.stopped = threading.Event()
        self.thread = None
        self.checking = False
        self.record = {'last_attempt': None, 'last_success': None, 'next_check': 0,
                       'latest': None, 'acknowledged': None, 'error': ''}
        try:
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(saved, dict):
                raise ValueError('Invalid saved check')
            for key in ('last_attempt', 'last_success', 'next_check'):
                if saved.get(key) is not None and not isinstance(saved[key], (int, float)):
                    raise ValueError('Invalid saved date')
            latest = saved.get('latest')
            if latest and (not isinstance(latest, dict) or not VERSION.fullmatch(str(latest.get('version', '')))):
                raise ValueError('Invalid saved release')
            self.record.update({k: v for k, v in saved.items() if k in self.record})
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError):
            self.record['error'] = 'Det gemte firmwaretjek kunne ikke læses. Et nyt tjek er planlagt.'

    def _save(self):
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.record, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)

    def snapshot(self):
        with self.lock:
            data = json.loads(json.dumps(self.record))
            data.update(enabled=self.enabled, checking=self.checking, interval_seconds=INTERVAL,
                        history_url=HISTORY_URL, download_url=DOWNLOAD_URL,
                        installed_version_known=False)
            data['unread'] = bool(data['latest'] and data['latest']['version'] != data['acknowledged'])
            return data

    def acknowledge(self):
        with self.lock:
            if self.record['latest']:
                self.record['acknowledged'] = self.record['latest']['version']
                self._save()
        return self.snapshot()

    def check(self, force=False):
        with self.lock:
            now = self.clock()
            if not self.enabled or self.checking or self.stopped.is_set():
                return self.snapshot()
            # Manual requests are bounded too; multiple teacher tabs share one check.
            last = self.record['last_attempt']
            if (force and last is not None and now - last < 60) or (not force and now < self.record['next_check']):
                return self.snapshot()
            self.record.update(last_attempt=now, next_check=now + INTERVAL)
            self._save()  # A restart never turns a failed attempt into a tight retry loop.
            self.checking = True
        try:
            latest = self.fetch()
            with self.lock:
                self.record.update(latest=latest, last_success=self.clock(), error='')
        except Exception:
            with self.lock:
                self.record['error'] = 'Bambus versionsliste kunne ikke hentes. Det er ikke bekræftet, om der er en ny version. Prøv igen senere.'
        finally:
            with self.lock:
                self.checking = False
                self._save()
        return self.snapshot()

    def start(self):
        if self.enabled and self.thread is None:
            self.thread = threading.Thread(target=self._run, name='firmware-check', daemon=True)
            self.thread.start()

    def _run(self):
        while not self.stopped.is_set():
            self.check()
            self.stopped.wait(60)

    def stop(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=12)
