r"""Install the student packages only on an unenrolled loopback test machine.

Run as root inside a disposable test installation (for example WSL Debian) that
has the synthetic fixture in /etc/elevhub. The script refuses to run on a
machine that is enrolled or that points at a real server.

Example:
    python3 install_student_packages.py /path/to/debian report.json \
        --expect makervaerkstedet-elev=2.0.0~20261112.7 \
        --expect makervaerkstedet-updater=1.0.0~20261112.5
"""
import argparse
import hashlib
import http.client
import json
import os
import subprocess
import sys
import time
from pathlib import Path

WORKSHOPS = ((8795, '/laser.html'), (8796, '/folie.html'), (8798, '/model3d.html'))


def check(condition, message):
    """Safety check that also holds under python -O (unlike assert)."""
    if not condition:
        raise AssertionError(message)


def parse_expect(value):
    name, separator, version = value.partition('=')
    if not separator or not name or not version:
        raise argparse.ArgumentTypeError('expected NAME=VERSION, got: ' + value)
    return name, version


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('packages', type=Path, help='folder with the .deb files (NAME_VERSION_*.deb)')
    parser.add_argument('report', type=Path, help='JSON report to write; the apt log goes next to it with .log')
    parser.add_argument('--expect', action='append', required=True, type=parse_expect, metavar='NAME=VERSION',
                        help='package and exact version that must be installed; repeat for each package')
    args = parser.parse_args(argv)
    expected = dict(args.expect)
    check(len(expected) == len(args.expect), 'The same package was given twice in --expect')
    check(args.packages.is_dir(), 'Package folder does not exist: ' + str(args.packages))

    profile = json.loads(Path('/etc/elevhub/kiosk.json').read_bytes())
    check(profile['server'] == '127.0.0.1', 'Only isolated loopback test fixtures')
    check(not Path('/etc/elevhub/enhed.json').exists(), 'No enrolled device permitted')
    files = [Path('/etc/elevhub/kiosk.json'), Path('/etc/elevhub/ca.pem'), Path('/etc/chromium/policies/managed/elevhub.json')]
    roots = [Path('/var/lib/elevhub-design/udbakke')] + sorted(Path('/home').glob('*/Projekter'))
    for root in roots:
        if root.exists():
            files.extend(p for p in root.rglob('*') if p.is_file())

    def hashes():
        return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}

    before = hashes()
    archives = []
    for name, version in expected.items():
        found = sorted(args.packages.glob(name + '_' + version + '_*.deb'))
        check(len(found) == 1, 'Expected exactly one package file for %s %s, found %d' % (name, version, len(found)))
        archives.append(found[0])
    with args.report.with_suffix('.log').open('w') as log:
        subprocess.run(['apt-get', '-y', '--no-remove', '--no-install-recommends', 'install', *map(str, archives)],
                       check=True, stdout=log, stderr=subprocess.STDOUT, env={**os.environ, 'DEBIAN_FRONTEND': 'noninteractive'})
        subprocess.run(['systemctl', 'daemon-reload'], check=True, stdout=log, stderr=subprocess.STDOUT)
        subprocess.run(['systemctl', 'restart', 'elevhub-lokal', 'elevhub-design'], check=True, stdout=log, stderr=subprocess.STDOUT)
    check(hashes() == before, 'Existing fixture state changed')
    for name, version in expected.items():
        check(subprocess.check_output(['dpkg-query', '-W', '-f=${Version}', name], text=True) == version,
              'Installed version of %s is not %s' % (name, version))
    check(not subprocess.check_output(['dpkg', '--audit']).strip(), 'dpkg --audit is not clean')
    for port, route in WORKSHOPS:
        for attempt in range(50):
            try:
                connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
                connection.request('GET', route)
                response = connection.getresponse()
                body = response.read()
                connection.close()
                if response.status == 200 and b'/_elevhub/projekter/bridge.js' in body:
                    break
            except OSError:
                pass
            time.sleep(.2)
        else:
            raise AssertionError('Installed workshop did not answer: ' + str(port))
    result = dict(versions=expected, preservedFiles=len(before), preservedHashes=before, dpkgAuditClean=True,
                  workshopHttpReady=[port for port, _ in WORKSHOPS],
                  packageHashes={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in archives})
    args.report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'preservedHashes'}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
