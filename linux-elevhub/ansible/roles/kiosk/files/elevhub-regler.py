#!/usr/bin/env python3
"""Elevhub-regler: håndhæver lærerens valg for Internet på elevcomputeren. Kører som root.

Hvert 3. sekund spørges elevhubben, om appen Internet er åben. Så sættes:

1. Chromiums URL-politik: /etc/chromium/policies/managed/elevhub-url.json
   (kopi af /etc/elevhub/url-aaben.json eller url-lukket.json). Chromium læser den igen af sig selv.
2. Chromiums download-mappe: en indsat USB-nøgle, ellers elevens Projekter
   (/etc/chromium/policies/managed/elevhub-download.json). Chromium gemmer uden at spørge.
3. En nftables-firewall, der KUN gælder elevkontoen:
     altid tilladt   lokale forbindelser, DNS, serverens elevporte
     altid spærret   andre adresser på skolens net (printere, router, serverens andre porte)
     internettet     åbent eller spærret efter lærerens valg
   Systemets egne tjenester (opdateringer, Ansible, tid) berøres ikke.

Uden forbindelse til elevhubben åbnes alle værksteder og internetadgang.
Når forbindelsen vender tilbage, anvendes lærerens aktuelle valg igen.

Den anvendte version skrives i /run/elevhub/anvendt.json efter en godkendt rootstatus.
Elevbjælken melder den tilbage, så lærerpanelet kun viser "Følger dine valg", når
reglerne faktisk er sat.

Styres af Ansible (rollen kiosk). Indstillinger: /etc/elevhub/kiosk.json
"""
import json
import ipaddress
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from elevhub_klient import hent_json, hent_offentlig_json, resolve_host, server_utilgaengelig  # noqa: E402
import importlib.util
_WEBPROXY_SPEC = importlib.util.spec_from_file_location(
    'elevhub_webproxy', Path(__file__).resolve().with_name('elevhub-webproxy.py'))
webproxy = importlib.util.module_from_spec(_WEBPROXY_SPEC)
_WEBPROXY_SPEC.loader.exec_module(webproxy)

KONF = {}  # Indlæses ved start, så regelgeneratoren kan testes uden systemfiler.
CREDENTIALS = {}
POLITIK = Path('/etc/chromium/policies/managed/elevhub-url.json')
DOWNLOAD = Path('/etc/chromium/policies/managed/elevhub-download.json')
URL_FILER = {True: Path('/etc/elevhub/url-aaben.json'), False: Path('/etc/elevhub/url-lukket.json')}
WEBPROXY_POLITIK = Path('/etc/elevhub/webproxy-policy.conf')
WEBPROXY_UNIT = Path('/etc/systemd/system/elevhub-webproxy.service')
ANVENDT = Path('/run/elevhub/anvendt.json')
RELEASE_FILE = Path('/etc/elevhub/release.json')
RELEASE_ID = re.compile(r'[A-Za-z0-9._-]{1,64}\Z')
PRIVATE_V4 = '10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16, 100.64.0.0/10'
PRIVATE_V6 = 'fc00::/7, fe80::/10'

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
log = logging.getLogger('elevhub-regler')

# The public interface of hent_valg remains (version, internet_open).  The
# complete validated snapshot is retained for the policy writers below.
AKTUELLE_APPS = []


def server_ip():
    try:
        return resolve_host(KONF['server'], timeout=4)
    except (OSError, TimeoutError, ValueError):
        log.warning('Kan ikke slå serveren %s op endnu; internet forbliver efter gældende regler', KONF['server'])
        return None


def dns_adresser(sti=Path('/etc/resolv.conf')):
    """Tillad DNS til de konfigurerede resolvere, ikke port 53 på alle maskiner."""
    adresser = set()
    try:
        for linje in sti.read_text(encoding='utf-8').splitlines():
            dele = linje.split()
            if len(dele) >= 2 and dele[0] == 'nameserver':
                try:
                    adresser.add(str(ipaddress.ip_address(dele[1])))
                except ValueError:
                    pass
    except OSError:
        pass
    return sorted(adresser)


def nft_regler(internet_aaben, server, uid, dns=None):
    if type(internet_aaben) is not bool or type(uid) is not int or uid < 0:
        raise ValueError('Ugyldig internettilstand eller elevkonto.')
    if not KONF.get('elev_porte') or any(type(p) is not int or not 1 <= p <= 65535 for p in KONF['elev_porte']):
        raise ValueError('Ugyldige elevporte.')
    porte = ', '.join(str(p) for p in KONF['elev_porte'])
    serverregel = f'ip daddr {ipaddress.IPv4Address(server)} tcp dport {{ {porte} }} accept' if server else ''
    dns_regler = []
    for adresse in dns_adresser() if dns is None else dns:
        adresse = ipaddress.ip_address(adresse)
        familie = 'ip' if adresse.version == 4 else 'ip6'
        for protokol in ('udp', 'tcp'):
            dns_regler.append(f'{familie} daddr {adresse} {protokol} dport 53 accept')
    dns_tekst = '\n        '.join(dns_regler)
    # Public traffic always goes through the loopback Squid listener.  The
    # internet toggle changes Squid's destination ACL, never a direct egress
    # hole in this per-student chain.
    sidste = 'reject'
    return f"""table inet elevhub
delete table inet elevhub
table inet elevhub {{
    chain ud {{
        type filter hook output priority filter; policy accept;
        meta skuid != {uid} accept
        oif "lo" accept
        {dns_tekst}
        {serverregel}
        ip daddr {{ {PRIVATE_V4} }} reject
        ip6 daddr {{ {PRIVATE_V6} }} reject
        {sidste}
    }}
}}
"""


def skriv_atomisk(maal, data, mode=0o644):
    midlertidig = maal.with_name('.' + maal.name + '.ny')
    midlertidig.write_bytes(data)
    os.chmod(midlertidig, mode)
    os.replace(midlertidig, maal)


def _chromium_url_policy(internet_aaben, apps):
    """Build URL policy with independent web workshop entries.

    Chromium policy paths are prefixes, not extension-style glob patterns.
    '/ *' (without the space) would match a literal star. A leading hostname
    dot makes these domain entries exact, matching the proxy's ACLs.
    https://www.chromium.org/administrators/url-blocklist-filter-format/
    """
    try:
        policy = json.loads(URL_FILER[internet_aaben].read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise ValueError('URL-politik mangler eller er ugyldig.') from error
    open_domains, closed_domains = webproxy.snapshot_domains(apps or [], internet_aaben)
    if internet_aaben:
        block = list(policy.get('URLBlocklist', []))
        for domain in closed_domains:
            block.append(f'https://.{domain}/')
        policy['URLBlocklist'] = sorted(set(block))
    else:
        allow = list(policy.get('URLAllowlist', []))
        for domain in open_domains:
            allow.append(f'https://.{domain}/')
        policy['URLAllowlist'] = sorted(set(allow))

    # Local editors have no central gateway to enforce their teacher choice.
    # Keep the project shelf (8791) reachable, while adding only currently
    # open local app URLs to the closed-internet allowlist.  With Internet
    # open, explicitly closed local apps are blocked again.  The loopback
    # service itself remains deliberately unaware of central policy.
    # An app counts as local when this computer has a local URL for its id —
    # also laser/folie, which stay "vaerksted" on the server because their
    # submissions still pass the central gateway.
    local_urls = KONF.get('lokale_app_urler', {})
    if isinstance(local_urls, dict):
        local_open, local_closed = [], []
        for app in apps or []:
            # micro:bit is still a web workshop in the teacher catalog, but
            # this pupil opens the installed controller at its local URL.
            if not isinstance(app, dict) or app.get('type') == 'browser':
                continue
            url = local_urls.get(app.get('id'))
            if not isinstance(url, str):
                continue
            try:
                parsed = urlsplit(url)
                if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1'
                        or parsed.port is None or parsed.path not in ('', '/')):
                    continue
                prefix = f'http://127.0.0.1:{parsed.port}/'
                aliases = [prefix, f'http://localhost:{parsed.port}/']
                (local_open if app.get('aaben') is True else local_closed).extend(aliases)
            except (TypeError, ValueError):
                continue
        if internet_aaben:
            policy['URLBlocklist'] = sorted(set(policy.get('URLBlocklist', []) + local_closed))
        else:
            policy['URLAllowlist'] = sorted(set(policy.get('URLAllowlist', []) + local_open))
    return json.dumps(policy, ensure_ascii=False, indent=2).encode('utf-8')


def _webproxy_apply(internet_aaben, apps, server):
    """Install and restart the isolated proxy after a valid policy change."""
    if not WEBPROXY_UNIT.exists():
        if KONF.get('webproxy_required') is True:
            raise OSError('Webproxy-tjenesten mangler.')
        return
    blocked = [server] if server else []
    config = webproxy.render_policy(apps or [], internet_aaben, blocked)
    skriv_atomisk(WEBPROXY_POLITIK, config.encode('utf-8'), mode=0o600)
    webproxy.service_restart(subprocess.run)


def _webproxy_stop():
    if not WEBPROXY_UNIT.exists():
        return KONF.get('webproxy_required') is not True
    try:
        webproxy.service_stop(subprocess.run)
        return True
    except (OSError, subprocess.SubprocessError) as error:
        log.error('Kunne ikke stoppe webproxy: %s', type(error).__name__)
        return False


def _webproxy_is_active():
    if not WEBPROXY_UNIT.exists():
        return KONF.get('webproxy_required') is not True
    try:
        return webproxy.service_is_active(subprocess.run)
    except (OSError, subprocess.SubprocessError):
        return False


def anvend(internet_aaben, server, uid, dns=None, apps=None):
    regler = nft_regler(internet_aaben, server, uid, dns)
    politik = _chromium_url_policy(internet_aaben, apps if apps is not None else AKTUELLE_APPS)
    # En delvist gennemført ændring må aldrig efterlade en gyldig kvittering.
    ANVENDT.unlink(missing_ok=True)
    # Closing an app must tear down existing CONNECT tunnels before applying
    # the new ACL.  A stopped proxy also provides a safe intermediate state if
    # nft or a later root-owned write fails.
    if not _webproxy_stop():
        raise OSError('Webproxy kunne ikke stoppes sikkert.')
    if not internet_aaben:
        # Closed URL policy is safe to install before nftables and leaves the
        # browser fail-closed even if the firewall transaction is rejected.
        skriv_atomisk(POLITIK, politik)
    subprocess.run(['nft', '-f', '-'], input=regler, text=True, check=True, timeout=10)
    try:
        skriv_atomisk(POLITIK, politik)
        _webproxy_apply(internet_aaben, apps if apps is not None else AKTUELLE_APPS, server)
    except (OSError, ValueError, subprocess.SubprocessError):
        # If the browser policy or proxy cannot be applied, close both paths.
        _webproxy_stop()
        subprocess.run(['nft', '-f', '-'], input=nft_regler(False, server, uid, dns),
                       text=True, check=True, timeout=10)
        raise
    log.info('Internet %s for %s', 'ÅBENT' if internet_aaben else 'LUKKET', KONF['elev'])


def usb_mappe():
    """Første USB-nøgle, som udiskie har monteret for eleven (/media/<elev>/...)."""
    try:
        with open('/proc/mounts', encoding='utf-8') as mounts:
            for linje in mounts:
                enhed, sted = linje.split()[:2]
                sted = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), sted)   # fx \040 = mellemrum
                if sted.startswith(f"/media/{KONF['elev']}/") and enhed.startswith('/dev/'):
                    # The micro:bit appears as a small USB mass-storage volume
                    # named MICROBIT.  Never redirect ordinary downloads there:
                    # its flash filesystem is the workshop device, not student
                    # project storage.
                    if Path(sted).name.upper().startswith('MICROBIT'):
                        continue
                    return sted
    except OSError:
        pass
    return None


def saet_download():
    import pwd
    mappe = usb_mappe() or os.path.join(pwd.getpwnam(KONF['elev']).pw_dir, 'Projekter')
    data = json.dumps({'DownloadDirectory': mappe}).encode()
    if not DOWNLOAD.exists() or DOWNLOAD.read_bytes() != data:
        skriv_atomisk(DOWNLOAD, data)
        log.info('Downloads gemmes i %s', mappe)


def meld_anvendt(version, internet_aaben):
    ANVENDT.parent.mkdir(parents=True, exist_ok=True)
    skriv_atomisk(ANVENDT, json.dumps({'version': version, 'internet': internet_aaben, 'tid': int(time.time())}).encode())


def hent_valg():
    global AKTUELLE_APPS
    try:
        if KONF.get('usikker_test') is True:
            # Kun det gamle, eksplicit HTTP-testinventory må bruge de offentlige routes.
            d = hent_offentlig_json(KONF['hub'], '/api/apps', usikker_test=True,
                                    timeout=4)
        else:
            d = hent_json(KONF['hub'], '/api/enhed/politik', token=CREDENTIALS['token'],
                          computer=CREDENTIALS['computer'], ca_fil=KONF['ca_fil'],
                          usikker_test=False, timeout=4)
    except Exception:
        ANVENDT.unlink(missing_ok=True)
        raise
    if (not isinstance(d, dict) or type(d.get('version')) is not int
            or d['version'] < 1 or not isinstance(d.get('apps'), list)):
        raise ValueError('Elevhubben sendte en ugyldig politik.')
    if any(not isinstance(a, dict) for a in d['apps']):
        raise ValueError('Ugyldig appliste.')
    # Validate every web workshop before touching nftables or Squid.  Other
    # workshop types remain backward compatible with the server's existing
    # schema, but a web app can never silently degrade to general Internet.
    for app in d['apps']:
        if app.get('type') == 'web':
            if type(app.get('aaben')) is not bool:
                raise ValueError('Ugyldigt webapp-valg.')
            webproxy.web_domains(app)
        elif 'domaener' in app:
            # The server deliberately exposes an empty domaener field for
            # legacy/local/browser apps.  Non-empty domains are meaningful
            # only for a web workshop and must not be silently accepted.
            if app.get('domaener') != []:
                raise ValueError('domaener må kun bruges af webapps.')
    AKTUELLE_APPS = d['apps']
    apps = [a for a in d['apps'] if a.get('id') == KONF['internet_app']]
    if len(apps) > 1 or (apps and type(apps[0].get('aaben')) is not bool):
        raise ValueError('Ugyldigt internetvalg.')
    return d['version'], apps[0]['aaben'] if apps else False


SSH_VAERTSNOEGLE = Path('/etc/ssh/ssh_host_ed25519_key.pub')


def ssh_vaertsnoegle():
    """The PUBLIC host key, so the admin can trust this PC by name, not by IP.

    It travels only in the device-authenticated status report; the admin
    fetches it from the server instead of trusting the first SSH answer.
    """
    try:
        felter = SSH_VAERTSNOEGLE.read_text(encoding='ascii').split()
    except (OSError, UnicodeError):
        return None
    if len(felter) >= 2 and felter[0] == 'ssh-ed25519' and re.fullmatch(r'[A-Za-z0-9+/]{68}', felter[1]):
        return felter[0] + ' ' + felter[1]
    return None


def software_version(sti=RELEASE_FILE):
    """Return the validated installed release id, or no version on bad input."""
    try:
        data = json.loads(sti.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    version = data.get('release_id')
    if not isinstance(version, str) or RELEASE_ID.fullmatch(version) is None:
        return None
    return version


def meld_status(version):
    """Kvitter kun efter politik og nftables er anvendt korrekt."""
    if KONF.get('usikker_test') is True:
        svar = hent_offentlig_json(
            KONF['hub'], '/api/status', usikker_test=True,
            data={'computer': KONF['computer'], 'kilde': 'bjaelke', 'version': version}, timeout=4)
    else:
        data = {'computer': CREDENTIALS['computer'], 'version': version}
        installed = software_version()
        if installed is not None:
            data['software_version'] = installed
        noegle = ssh_vaertsnoegle()
        if noegle:
            data['ssh_host_key'] = noegle
        svar = hent_json(KONF['hub'], '/api/enhed/status', token=CREDENTIALS['token'],
                         computer=CREDENTIALS['computer'], ca_fil=KONF['ca_fil'],
                         usikker_test=False, data=data, timeout=4)
    if (not isinstance(svar, dict) or type(svar.get('version')) is not int or
            svar['version'] < 0 or not isinstance(svar.get('apps'), list) or
            type(svar.get('gaa_hjem')) is not bool):
        raise ValueError('Elevhub sendte et ugyldigt status-svar.')
    return svar


def luk_ved_fejl(server, uid, dns):
    """Luk elevnettet og fjern rootstatus efter enhver ugyldig kontakt."""
    global AKTUELLE_APPS
    ANVENDT.unlink(missing_ok=True)
    try:
        # Do not reuse the last authenticated workshop ACL after a stale
        # contact.  A closed policy is written, then the proxy is stopped so
        # existing CONNECT tunnels cannot survive the failure window.
        AKTUELLE_APPS = []
        anvend(False, server, uid, dns)
        if not _webproxy_stop():
            return False
        # Version 0 er en eksplicit lukningskvittering, hvis hubben kan nås.
        try:
            meld_status(0)
        except Exception:
            pass
        return True
    except (OSError, ValueError, subprocess.SubprocessError) as fejl:
        log.error('Kunne ikke lukke elevnettet efter fejl: %s', type(fejl).__name__)
        return False


# After a valid teacher policy, a single missed reply must not open Internet:
# a pupil could starve the CPU on purpose until one 4 s request times out.
# Only this many consecutive transport failures count as "server down". A cold
# boot that never reached the server still opens at once (offline rule).
FEJL_FOER_OFFLINE = 3


def main(kun_sikker_start=False):
    import pwd
    global AKTUELLE_APPS
    uid = pwd.getpwnam(KONF['elev']).pw_uid
    fejl_i_traek = 0
    online_senest = False
    # The same bounded check runs before login and during normal operation.
    # Cold offline boot needs neither a cached teacher policy nor a DNS lookup.
    try:
        server = str(ipaddress.IPv4Address(KONF['server']))
    except ValueError:
        server = None
    dns = dns_adresser()
    anvendt, apps_signatur = None, None
    while True:
        try:
            saet_download()
        except OSError as fejl:
            log.error('Kunne ikke sætte download-mappen: %s', type(fejl).__name__)
        gyldig = False
        try:
            version, aaben = hent_valg()
            gyldig = True
            ny_signatur = json.dumps(AKTUELLE_APPS, sort_keys=True, ensure_ascii=False)
        except Exception as fejl:
            utilgaengelig = server_utilgaengelig(fejl)
            if utilgaengelig and online_senest and not kun_sikker_start:
                fejl_i_traek += 1
                if fejl_i_traek < FEJL_FOER_OFFLINE:
                    log.debug('Elevhub svarede ikke (%s/%s); lærerens valg bevares.',
                              fejl_i_traek, FEJL_FOER_OFFLINE)
                    time.sleep(3)
                    continue
            ANVENDT.unlink(missing_ok=True)
            aaben = utilgaengelig
            AKTUELLE_APPS = []  # Never carry a stale closed app into offline mode.
            ny_signatur = 'offline' if aaben else 'afvist'
            online_senest = False
            log.debug('Elevhub-kontakt: %s', type(fejl).__name__)
        try:
            ny_server = (server_ip() or server) if gyldig else server
            ny_dns = dns_adresser()
            if (aaben != anvendt or ny_server != server or ny_dns != dns
                    or ny_signatur != apps_signatur or not _webproxy_is_active()):
                anvend(aaben, ny_server, uid, ny_dns)
                anvendt, server, dns = aaben, ny_server, ny_dns
                apps_signatur = ny_signatur
                if ny_signatur == 'offline':
                    log.info('Ingen serverforbindelse: alle værksteder er åbne.')
            if not _webproxy_is_active():
                raise OSError('Webproxy-tjenesten er ikke aktiv.')
        except (OSError, ValueError, subprocess.SubprocessError) as fejl:
            # A local write/service failure is not a lost server connection.
            ANVENDT.unlink(missing_ok=True)
            anvendt, apps_signatur = None, None
            _webproxy_stop()
            if kun_sikker_start:
                raise
            log.error('Kunne ikke sætte reglerne: %s', type(fejl).__name__)
            time.sleep(3)
            continue
        if gyldig:
            try:
                meld_status(version)
            except Exception as fejl:
                if (server_utilgaengelig(fejl) and online_senest and not kun_sikker_start
                        and fejl_i_traek + 1 < FEJL_FOER_OFFLINE):
                    fejl_i_traek += 1
                    time.sleep(3)
                    continue
                ANVENDT.unlink(missing_ok=True)
                online_senest = False
                if server_utilgaengelig(fejl):
                    AKTUELLE_APPS = []
                    try:
                        anvend(True, server, uid, dns)
                        anvendt, apps_signatur = True, 'offline'
                    except (OSError, ValueError, subprocess.SubprocessError):
                        anvendt, apps_signatur = None, None
                        if kun_sikker_start:
                            raise
                else:
                    lukket = luk_ved_fejl(server, uid, dns)
                    anvendt, apps_signatur = (False if lukket else None), None
            else:
                meld_anvendt(version, anvendt)
                fejl_i_traek, online_senest = 0, True
        if kun_sikker_start:
            return
        time.sleep(3)


if __name__ == '__main__':
    import sys
    with open('/etc/elevhub/kiosk.json', encoding='utf-8') as f:
        KONF = json.load(f)
    try:
        with open('/etc/elevhub/enhed.json', encoding='utf-8') as f:
            CREDENTIALS = json.load(f)
    except (OSError, ValueError, TypeError) as fejl:
        # Manglende credentials er en konfigurationsfejl, ikke en netafbrydelse.
        CREDENTIALS = {}
        log.error('Enhedscredentials kunne ikke læses (%s)', type(fejl).__name__)
    if not shutil.which('nft'):
        raise SystemExit('nftables mangler (apt install nftables)')
    main(kun_sikker_start='--sikker-start' in sys.argv)
