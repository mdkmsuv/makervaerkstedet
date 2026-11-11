"""Rigtige nftables-pakkeprøver i en ISOLERET Linux-netværksnamespace.

Kør kun med: sudo unshare --net -- python3 tests/controleer_nft_linux.py --geisoleerd
Netværksinterfacet her har ingen forbindelse til VM'ens eller skolens netværk.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def koer(*cmd, **kwargs):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=10, **kwargs)


def taellere():
    data = json.loads(koer('nft', '-j', 'list', 'table', 'inet', 'elevhub').stdout)
    samlet = afvist = 0
    for element in data['nftables']:
        udtryk = element.get('rule', {}).get('expr', [])
        pakker = sum(e.get('counter', {}).get('packets', 0) for e in udtryk)
        samlet += pakker
        if any('reject' in e for e in udtryk):
            afvist += pakker
    return samlet, afvist


def main():
    if (sys.platform != 'linux' or os.geteuid() != 0 or '--geisoleerd' not in sys.argv
            or os.readlink('/proc/self/ns/net') == os.readlink('/proc/1/ns/net')):
        raise SystemExit('Afvist: prøven kræver root i en særskilt netværksnamespace (unshare --net).')
    fil = Path(__file__).parents[1] / 'ansible/roles/kiosk/files/elevhub-regler.py'
    spec = importlib.util.spec_from_file_location('regler', fil)
    regler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(regler)
    regler.KONF = {'elev_porte': [8770, 8771, 8772, 8773]}
    koer('ip', 'link', 'set', 'lo', 'up')
    koer('ip', 'link', 'add', 'elevhubtest', 'type', 'dummy')
    koer('ip', 'link', 'set', 'elevhubtest', 'up')
    koer('ip', 'addr', 'add', '192.0.2.2/24', 'dev', 'elevhubtest')
    koer('ip', 'route', 'add', 'default', 'dev', 'elevhubtest')
    koer('ip', '-6', 'addr', 'add', '2001:db8:1::2/64', 'dev', 'elevhubtest', 'nodad')
    koer('ip', '-6', 'route', 'add', 'default', 'dev', 'elevhubtest')
    # Undgå ARP/NDP-ventetid før pakken når output-filteret i det tomme net.
    for ip in ('198.51.100.8', '203.0.113.1', '203.0.113.20', '192.0.2.53', '198.51.100.53',
               '2001:db8:2::8', 'fd00::20'):
        koer('ip', 'neigh', 'replace', ip, 'lladdr', '02:00:00:00:00:01', 'nud', 'permanent', 'dev', 'elevhubtest')
    sender = '''import os,socket,sys
os.setgid(int(sys.argv[1])); os.setuid(int(sys.argv[1]))
tcp = sys.argv[4] == 'tcp'
s=socket.socket(socket.AF_INET6 if ':' in sys.argv[2] else socket.AF_INET,socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM)
s.settimeout(0.15)
try:
    if tcp:
        s.connect((sys.argv[2],int(sys.argv[3])))
    else:
        s.sendto(b'elevhub-test',(sys.argv[2],int(sys.argv[3])))
    print('tilladt')
except TimeoutError:
    print('timeout')
except OSError:
    print('afvist')
'''
    # Prøven viser firewallens afvisning/tilladelse, ikke en komplet forbindelse
    # til et værksted eller til internettet; dummy-nettet har ingen modtager.
    antal = 0
    for aaben in (False, True, False):
        tekst = regler.nft_regler(aaben, '203.0.113.1', 65534, ['192.0.2.53'])
        koer('nft', '-c', '-f', '-', input=tekst)
        # Instrumentér kun tællere; bevar de genererede match og afgørelser.
        tekst = re.sub(r'(?m)^(\s+)([^\n]*?)(accept|reject)$', r'\1\2counter \3', tekst)
        koer('nft', '-f', '-', input=tekst)
        for uid, ip, port, protokol, tilladt in (
            (65534, '198.51.100.8', 443, 'udp', aaben),
            (65534, '2001:db8:2::8', 443, 'udp', aaben),
            (65534, '203.0.113.1', 8780, 'tcp', False),
            (65534, '203.0.113.20', 443, 'tcp', False),
            (65534, 'fd00::20', 443, 'udp', False),
            (65534, '192.0.2.53', 53, 'udp', True),
            (65534, '198.51.100.53', 53, 'udp', aaben),
            (0, '198.51.100.8', 443, 'tcp', True),
            (65534, '203.0.113.1', 8770, 'tcp', True),
            (65534, '203.0.113.1', 8773, 'tcp', True),
            (65534, '198.51.100.8', 443, 'tcp', aaben),
            (65534, '2001:db8:2::8', 443, 'tcp', aaben),
        ):
            foer, afvist_foer = taellere()
            socketresultat = koer(sys.executable, '-c', sender, str(uid), ip, str(port), protokol).stdout.strip()
            efter, afvist_efter = taellere()
            # TCP/IPv6 kan behandle ICMP-afvisning som en midlertidig fejl og ende
            # i timeout. Kernelens tællere giver den faktiske filterafgørelse.
            resultat = afvist_efter == afvist_foer
            if efter <= foer or resultat != tilladt:
                print(koer('nft', 'list', 'table', 'inet', 'elevhub').stdout)
                raise AssertionError((aaben, uid, ip, port, protokol, resultat, tilladt, socketresultat))
            antal += 1
    print(json.dumps({'ok': True, 'pakkeproever': antal, 'namespace': 'isoleret',
                      'ipv4': True, 'ipv6': True, 'sekvens': 'lukket-aaben-lukket'}))


if __name__ == '__main__':
    main()
