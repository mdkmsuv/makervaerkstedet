#!/usr/bin/env python3
"""Finish a bundled student installation; run by systemd, not by the pupil."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import time

BUNDLE = Path('/opt/elevhub-install')
CONFIG = Path('/etc/elevhub-install')
STATE = Path('/var/lib/elevhub')
NAME = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$')
TOKEN = re.compile(r'^[A-Za-z0-9_-]{43,128}$')
THEMES = {'red', 'yellow', 'green'}


def validate_profile(settings, device, ca, public_key):
    if not isinstance(settings, dict) or not isinstance(device, dict):
        raise ValueError('Installationens indstillinger og enhedsfil skal være JSON-objekter.')
    name = settings.get('computer', '')
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError('Computernavnet skal være 1-32 små bogstaver, tal eller bindestreger.')
    ipaddress.IPv4Address(settings['server'])
    if device.get('computer') != name:
        raise ValueError('Enhedsfilens computernavn passer ikke til installationen.')
    for field in ('token', 'ui_token'):
        if not isinstance(device.get(field), str) or not TOKEN.fullmatch(device[field]):
            raise ValueError('Enhedsfilens adgangsværdier er ugyldige.')
    if device['token'] == device['ui_token']:
        raise ValueError('Enhedens to adgangsværdier skal være forskellige.')
    if '-----BEGIN CERTIFICATE-----' not in ca or 'PRIVATE KEY' in ca:
        raise ValueError('Vælg serverens offentlige CA-certifikat.')
    if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]+(?: [\w.@-]+)?', public_key.strip()):
        raise ValueError('Vælg den offentlige Ed25519-administrationsnøgle.')
    admin = settings.get('local_admin')
    if admin is not None:
        if not isinstance(admin, dict) or set(admin) != {'username', 'password_hash'}:
            raise ValueError('Lokal admin kræver username og password_hash, aldrig en klar adgangskode.')
        username, password_hash = admin['username'], admin['password_hash']
        if (not isinstance(username, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', username)
                or username in ('root', 'elev', 'elevhub')):
            raise ValueError('Lokal admin skal have sit eget gyldige kontonavn.')
        if (not isinstance(password_hash, str)
                or not re.fullmatch(r'\$(?:6|y)\$[A-Za-z0-9./$=,-]{20,1024}', password_hash)):
            raise ValueError('Lokal admin kræver en SHA-512- eller yescrypt-adgangskodehash.')
    school_name = settings.get('school_name', '')
    if school_name and (not isinstance(school_name, str) or not 1 <= len(school_name) <= 120
                        or any(ord(char) < 32 or ord(char) == 127 for char in school_name)):
        raise ValueError('Skolenavnet skal være 1–120 tegn uden kontroltegn.')
    theme = settings.get('theme', 'red')
    if not isinstance(theme, str) or theme not in THEMES:
        raise ValueError('Temaet skal være red, yellow eller green.')
    if 'admin_sources' in settings:
        sources = settings['admin_sources']
        if (not isinstance(sources, list) or not sources
                or any(not isinstance(source, str) for source in sources)):
            raise ValueError('admin_sources skal være en ikke-tom liste af IP-adresser.')
        for source in sources:
            try:
                ipaddress.ip_address(source)
            except ValueError as error:
                raise ValueError('admin_sources skal indeholde IP-adresser.') from error


def make_variables(settings, public_key, manifest, bundle=BUNDLE, config=CONFIG):
    assets = bundle / 'assets'
    variables = {
        'ansible_connection': 'local', 'ansible_python_interpreter': '/usr/bin/python3',
        'elevhub_firstboot': True,
        'grundsystem_driftsnoegle': public_key.strip(),
        'kiosk_server': settings['server'], 'kiosk_protokol': 'https',
        'kiosk_usikker_test': False,
        'kiosk_enhedsfil': str(config / 'device.json'), 'kiosk_ca_fil': str(config / 'ca.crt'),
        'kiosk_lokale_app_kilder': {
            'scratch': str(assets / 'scratch'), 'datatraener': str(assets / 'datatraener'),
            'kredsloeb': str(bundle / 'linux-elevhub/server/kredsloeb'),
            'microbit': str(bundle / 'linux-elevhub/server/microbit-controller'),
        },
        'cadoodle_aktiveret': True,
        'cadoodle_deb_kilde': str(assets / 'CaDoodle.deb'),
        'cadoodle_bootstrap_zip': str(assets / 'CaDoodle-ApplicationInstall.zip'),
        'cadoodle_bootstrap_zip_sha256': manifest['files']['assets/CaDoodle-ApplicationInstall.zip'],
        'cadoodle_kloster_jar': str(assets / 'CaDoodle-Kloster.jar'),
        'cadoodle_print_url': 'https://' + settings['server'] + ':8770',
        'cadoodle_print_ca': '/etc/elevhub/ca.pem',
        'kiosk_school_name': settings.get('school_name', ''),
        'kiosk_theme': settings.get('theme', 'red'),
    }
    # New onboarding profiles explicitly carry the controller allowlist. Keep
    # legacy profiles byte-for-byte compatible by omitting the variable when
    # they do not provide it.
    if 'admin_sources' in settings:
        variables['grundsystem_admin_kilder'] = list(settings['admin_sources'])
    elif settings.get('onboarding'):
        variables['grundsystem_admin_kilder'] = [settings['server']]
    if settings.get('local_admin'):
        variables.update(adminkonto_aktiveret=True,
                         adminkonto_navn=settings['local_admin']['username'],
                         adminkonto_adgangskode_hash=settings['local_admin']['password_hash'])
    return variables


def verify_bundle(bundle):
    manifest = json.loads((bundle / 'manifest.json').read_text())
    for name, expected in manifest['files'].items():
        source = bundle / name
        path = source.resolve()
        if (not path.is_relative_to(bundle.resolve()) or source.is_symlink()
                or any(parent.is_symlink() for parent in source.parents if parent != bundle.parent)):
            raise ValueError('Ugyldig filsti i installationspakken.')
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != expected:
            raise ValueError('Kontrolsum passer ikke: ' + name)
    return manifest


def main():
    if os.geteuid() != 0:
        raise SystemExit('Installationen skal køres af systemtjenesten som root.')
    STATE.mkdir(mode=0o755, parents=True, exist_ok=True)
    completed = STATE / 'installation-complete.json'
    if completed.exists():
        return
    print('Elevhub: installerer CaDoodle og elevens værksteder. SSH er tilgængelig imens.', flush=True)
    settings = json.loads((CONFIG / 'settings.json').read_text())
    device = json.loads((CONFIG / 'device.json').read_text())
    ca = (CONFIG / 'ca.crt').read_text()
    public_key = (CONFIG / 'admin.pub').read_text().strip()
    validate_profile(settings, device, ca, public_key)
    manifest = verify_bundle(BUNDLE)
    variables = make_variables(settings, public_key, manifest)
    inventory = {'elevcomputere': {'hosts': {settings['computer']: {'ansible_connection': 'local'}}}}
    for filename, data in [('inventory.json', inventory), ('variables.json', variables)]:
        path = CONFIG / filename
        path.write_text(json.dumps(data, indent=2) + '\n')
        path.chmod(0o600)
    env = os.environ.copy()
    env['ANSIBLE_CONFIG'] = str(BUNDLE / 'linux-elevhub/ansible/ansible.cfg')
    env['DEBIAN_FRONTEND'] = 'noninteractive'
    ansible = BUNDLE / 'linux-elevhub/ansible'
    subprocess.run([
        'ansible-playbook', str(ansible / 'site.yml'),
        '-i', str(CONFIG / 'inventory.json'), '-e', '@' + str(CONFIG / 'variables.json'),
    ], cwd=ansible, env=env, check=True)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    # On generic media tty1 is disabled while the text onboarding owns it.
    # Make the kiosk getty persistent before restarting it, then remove the
    # one-shot onboarding unit from future boot jobs.
    if Path('/etc/systemd/system/elevhub-onboarding.service').is_file():
        subprocess.run(['systemctl', 'enable', 'getty@tty1.service'], check=True)
        subprocess.run(['systemctl', 'disable', 'elevhub-onboarding.service'], check=True)
    subprocess.run(['systemctl', 'restart', 'getty@tty1.service'], check=True)
    # Success is recorded only after every role, handler and kiosk start succeeds.
    temporary = completed.with_suffix('.pending')
    temporary.write_text(json.dumps({'computer': settings['computer'], 'completed_at': int(time.time()),
                                    'bundle': manifest['version']}, indent=2) + '\n')
    temporary.chmod(0o644)
    os.replace(temporary, completed)
    print('Elevhub: installationen er færdig. Elevskærmen starter.', flush=True)


if __name__ == '__main__':
    main()
