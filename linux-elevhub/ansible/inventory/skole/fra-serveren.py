#!/usr/bin/env python3
"""Dynamisk computerliste: hent elevcomputerne fra skoleserverens Elevhub.

Elevcomputerne behøver ikke faste IP-adresser. Hver computer melder sig hvert
tredje sekund hos Elevhub med sin egen nøgle over HTTPS, og serveren gemmer
computerens navn, dens aktuelle adresse og dens offentlige SSH-værtsnøgle i
computere.json. Denne liste hentes over den eksisterende, fastlåste
SSH-forbindelse til serveren.

SSH til elevcomputerne godkendes derfor under computerens NAVN
(HostKeyAlias), ikke dens skiftende IP, og med den nøgle serveren har
modtaget fra netop den computer — ikke med det første svar på netværket.

Brug:
    koer.sh koer -i inventory/skole/fra-serveren.py
    python3 inventory/skole/fra-serveren.py --vis     (oversigt til mennesker)

Indstillinger (miljøvariabler; serverens SSH-adresse skal angives):
    ELEVHUB_SERVER_SSH          admin@server.example.invalid (erstat med egen server)
    ELEVHUB_SERVER_NOEGLE       ~/.ssh/printvaerksted_vm_ed25519
    ELEVHUB_SERVER_KNOWN_HOSTS  ~/.ssh/printvaerksted_vm_known_hosts
    ELEVHUB_COMPUTERE           C:\\ProgramData\\ElevhubServer\\hub\\computere.json
    ELEVHUB_ONLINE_MINUTTER     15
    ELEVHUB_ELEV_KNOWN_HOSTS    ~/.ssh/elevhub_elevcomputere_known_hosts

Findes serverens nøgle ikke i WSL, bruges Windows' egen OpenSSH med nøglen og
known_hosts fra Windows-profilens .ssh-mappe. Så skal den private nøgle ikke
kopieres ind i WSL.
"""
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

NAVN = re.compile(r'^vaerksted-[0-9]{2}$')
VAERTSNOEGLE = re.compile(r'^ssh-ed25519 [A-Za-z0-9+/]{68}$')


WINDOWS_SSH = '/mnt/c/Windows/System32/OpenSSH/ssh.exe'
NOEGLE, KENDTE = 'printvaerksted_vm_ed25519', 'printvaerksted_vm_known_hosts'


def windows_profil():
    """The Windows user's profile folder when running in WSL, else None."""
    try:
        svar = subprocess.run(['cmd.exe', '/c', 'echo %USERPROFILE%'], capture_output=True, timeout=10, cwd='/mnt/c')
    except (OSError, subprocess.TimeoutExpired):
        return None
    sti = svar.stdout.decode('utf-8', 'replace').strip()
    return sti if re.fullmatch(r'[A-Za-z]:\\[^\r\n%"]+', sti) else None


def indstillinger():
    env = os.environ.get
    server = env('ELEVHUB_SERVER_SSH', '').strip()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]{0,253}', server):
        raise ValueError('Angiv ELEVHUB_SERVER_SSH med skolens egen SSH-bruger og serveradresse.')
    ind = {
        'ssh': 'ssh',
        'server': server,
        'noegle': os.path.expanduser(env('ELEVHUB_SERVER_NOEGLE', '~/.ssh/' + NOEGLE)),
        'kendte': os.path.expanduser(env('ELEVHUB_SERVER_KNOWN_HOSTS', '~/.ssh/' + KENDTE)),
        'fil': env('ELEVHUB_COMPUTERE', r'C:\ProgramData\ElevhubServer\hub\computere.json'),
        'minutter': float(env('ELEVHUB_ONLINE_MINUTTER', '15')),
        'elev_kendte': Path(os.path.expanduser(env('ELEVHUB_ELEV_KNOWN_HOSTS', '~/.ssh/elevhub_elevcomputere_known_hosts'))),
    }
    if not env('ELEVHUB_SERVER_NOEGLE') and not os.path.isfile(ind['noegle']) and os.path.isfile(WINDOWS_SSH):
        profil = windows_profil()
        if profil:
            ind.update(ssh=WINDOWS_SSH, noegle=profil + '\\.ssh\\' + NOEGLE,
                       kendte=env('ELEVHUB_SERVER_KNOWN_HOSTS', profil + '\\.ssh\\' + KENDTE))
    return ind


def hent(ind):
    kommando = [ind['ssh'], '-i', ind['noegle'], '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
                '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=' + ind['kendte'],
                '-o', 'ConnectTimeout=10', ind['server'], 'type "' + ind['fil'] + '"']
    svar = subprocess.run(kommando, capture_output=True, timeout=40)
    if svar.returncode:
        fejl = svar.stderr.decode('utf-8', 'replace').strip()[:300]
        sys.exit('Kunne ikke hente computerlisten fra serveren (' + ind['server'] + '): ' + fejl)
    return json.loads(svar.stdout.decode('utf-8-sig'))


def inventar(data, nu, ind):
    """Return (hostvars, known_hosts lines, notes) for recently seen student PCs."""
    hostvars, kendte, noter = {}, [], []
    for navn, computer in sorted((data or {}).items()):
        if not NAVN.fullmatch(str(navn)) or not isinstance(computer, dict):
            continue
        sidst = computer.get('sidst_set')
        if not isinstance(sidst, (int, float)) or nu - sidst > ind['minutter'] * 60:
            noter.append(f'{navn}: ikke set de sidste {ind["minutter"]:g} minutter (slukket?)')
            continue
        try:
            ip = ipaddress.IPv4Address(computer.get('ip'))
        except (ipaddress.AddressValueError, ValueError, TypeError):
            noter.append(f'{navn}: ugyldig adresse fra serveren')
            continue
        if not ip.is_private or ip.is_loopback:
            noter.append(f'{navn}: adressen {ip} er ikke en adresse på skolens net')
            continue
        noegle = computer.get('ssh_host_key')
        hostvars[navn] = {
            'ansible_host': str(ip),
            'ansible_ssh_common_args': ('-o HostKeyAlias=' + navn + ' -o StrictHostKeyChecking=yes'
                                        ' -o UserKnownHostsFile=' + str(ind['elev_kendte'])),
        }
        if isinstance(noegle, str) and VAERTSNOEGLE.fullmatch(noegle):
            kendte.append(navn + ' ' + noegle)
        else:
            noter.append(f'{navn}: serveren har ikke modtaget dens SSH-nøgle endnu; forbindelsen afvises')
    return hostvars, kendte, noter


def gem_kendte(sti, linjer):
    sti.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    midlertidig = sti.with_name(sti.name + '.ny')
    midlertidig.write_text(''.join(linje + '\n' for linje in linjer), encoding='ascii')
    midlertidig.chmod(0o600)
    os.replace(midlertidig, sti)


def main(argv):
    ind = indstillinger()
    if argv[1:2] == ['--host']:
        print('{}')
        return
    hostvars, kendte, noter = inventar(hent(ind), time.time(), ind)
    gem_kendte(ind['elev_kendte'], kendte)
    if argv[1:2] == ['--vis']:
        for navn, v in hostvars.items():
            print(f'{navn:14} {v["ansible_host"]}')
        for note in noter:
            print(note)
        print(f'{len(hostvars)} elevcomputere klar.')
        return
    for note in noter:
        print('Elevhub: ' + note, file=sys.stderr)
    print(json.dumps({'elevcomputere': {'hosts': sorted(hostvars)},
                      '_meta': {'hostvars': hostvars}}, ensure_ascii=False))


if __name__ == '__main__':
    main(sys.argv)
