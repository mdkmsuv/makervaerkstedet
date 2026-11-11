#!/usr/bin/env python3
"""Fælles, lille HTTPS-klient til Elevhub på kiosken.

Modulet er med vilje uden logging af requests.  Tokens sendes kun i headers og
bliver aldrig en del af en exception eller en loglinje. Hver DNS/HTTP-operation
har en samlet 4-sekunders vægurtgrænse, så en trickle-forbindelse ikke kan holde
en overgang mellem online- og offlinetilstand åben.
"""
import json
import re
import ssl
import base64
import subprocess
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit

_COMPUTER = re.compile(r'^[a-z0-9][a-z0-9-]{0,31}$')
_TOKEN = re.compile(r'^[A-Za-z0-9_-]{43,128}$')


class RedirectError(OSError):
    """En privileged request må ikke følge en redirect."""


def server_utilgaengelig(fejl):
    """Transportfejl betyder offline; afvist login er stadig et serversvar."""
    if isinstance(fejl, urllib.error.HTTPError):
        return 500 <= fejl.code < 600
    if isinstance(fejl, (RedirectError, ssl.SSLError)):
        return False
    if isinstance(fejl, urllib.error.URLError):
        return server_utilgaengelig(fejl.reason)
    return isinstance(fejl, OSError)


class _AfvisRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RedirectError(f'Elevhub redirect afvist ({code})')


def valider_hub(hub, usikker_test=False):
    """Validér base-URL og returnér den uden trailing slash.

    HTTP accepteres kun når det er eksplicit slået til for et gammelt testmiljø.
    Der accepteres ikke credentials, query eller fragment i URL'en.
    """
    if not isinstance(hub, str):
        raise ValueError('Elevhub-URL mangler.')
    dele = urlsplit(hub)
    if dele.scheme not in ('https', 'http'):
        raise ValueError('Elevhub skal bruge https.')
    if dele.scheme == 'http' and usikker_test is not True:
        raise ValueError('HTTP er kun tilladt i eksplicit testtilstand.')
    if usikker_test is True and dele.scheme != 'http':
        raise ValueError('Testtilstand kræver HTTP.')
    if not dele.hostname or dele.username or dele.password or dele.query or dele.fragment:
        raise ValueError('Ugyldig Elevhub-URL.')
    try:
        port = dele.port
    except ValueError as fejl:
        raise ValueError('Ugyldig Elevhub-port.') from fejl
    if port is None or not 1 <= port <= 65535:
        raise ValueError('Ugyldig Elevhub-port.')
    return urlunsplit((dele.scheme, dele.netloc, dele.path.rstrip('/'), '', ''))


def ssl_kontekst(ca_fil):
    """Opret en verificerende SSL-kontekst med controllerens CA."""
    if not isinstance(ca_fil, str) or not ca_fil:
        raise ValueError('CA-fil mangler.')
    kontekst = ssl.create_default_context(cafile=ca_fil)
    kontekst.check_hostname = True
    kontekst.verify_mode = ssl.CERT_REQUIRED
    return kontekst


def _url(hub, sti, usikker_test):
    if not isinstance(sti, str) or not sti.startswith('/') or '://' in sti:
        raise ValueError('Ugyldig Elevhub-sti.')
    return valider_hub(hub, usikker_test) + sti


def _direct_request(url, headers, body, timeout, ca_fil):
    """Worker-side request. The parent process kills this process on timeout."""
    req = urllib.request.Request(url, data=body, headers=headers,
                                 method='POST' if body is not None else 'GET')
    if urlsplit(url).scheme == 'http':
        opener = urllib.request.build_opener(_AfvisRedirect())
    else:
        opener = urllib.request.build_opener(
            _AfvisRedirect(), urllib.request.HTTPSHandler(context=ssl_kontekst(ca_fil)))
    with opener.open(req, timeout=timeout) as svar:
        return svar.read(65537)


def _worker_json(config):
    try:
        body = (None if config.get('body') is None
                else base64.b64decode(config['body'], validate=True))
        payload = _direct_request(config['url'], config['headers'], body,
                                  config['timeout'], config.get('ca_fil', ''))
        return {'ok': True, 'payload': base64.b64encode(payload).decode('ascii')}
    except urllib.error.HTTPError as fejl:
        return {'ok': False, 'kind': 'http', 'url': fejl.url, 'code': fejl.code,
                'reason': str(fejl.reason)}
    except urllib.error.URLError as fejl:
        return {'ok': False, 'kind': 'error', 'type': type(fejl.reason).__name__,
                'reason': str(fejl.reason)}
    except Exception as fejl:
        return {'ok': False, 'kind': 'error', 'type': type(fejl).__name__,
                'reason': str(fejl)}


def _run_worker(config, timeout):
    """Run one isolated network operation; kill it so no late I/O can occur."""
    command = [sys.executable, str(__file__), '--elevhub-worker']
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, creationflags=flags)
    except TypeError:  # Some test doubles/platforms do not accept creationflags.
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL)
    request = json.dumps(config, ensure_ascii=False).encode('utf-8')
    try:
        output, _ = process.communicate(request, timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise TimeoutError('Elevhub request overskred tidsgrænsen.')
    if process.returncode != 0:
        raise OSError('Elevhub worker stoppede uventet.')
    try:
        result = json.loads(output)
    except (TypeError, ValueError) as fejl:
        raise OSError('Elevhub worker sendte ikke et svar.') from fejl
    if not result.get('ok'):
        if result.get('kind') == 'http':
            raise urllib.error.HTTPError(result.get('url', ''), result.get('code', 502),
                                         result.get('reason', 'HTTP-fejl'), {}, None)
        if result.get('type') == 'RedirectError':
            raise RedirectError('Elevhub redirect afvist.')
        if result.get('type') in ('SSLError', 'SSLCertVerificationError', 'CertificateError'):
            raise ssl.SSLError('Elevhub TLS-verifikation fejlede.')
        if result.get('type') in ('ValueError', 'FileNotFoundError', 'PermissionError'):
            raise ValueError('Elevhub-klientens lokale konfiguration er ugyldig.')
        raise OSError(result.get('reason') or 'Elevhub request fejlede.')
    try:
        return base64.b64decode(result['payload'], validate=True)
    except (KeyError, ValueError) as fejl:
        raise OSError('Elevhub worker sendte et ugyldigt payload.') from fejl


def _json_request(url, headers, body, timeout, ca_fil):
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError('Ugyldig request-timeout.')
    config = {'url': url, 'headers': headers, 'timeout': timeout, 'ca_fil': ca_fil,
              'body': None if body is None else base64.b64encode(body).decode('ascii')}
    return _run_worker(config, timeout)


def resolve_host(host, timeout=4):
    """Resolve in an isolated process so a stuck resolver leaves no thread behind."""
    config = {'host': host}
    command = [sys.executable, str(__file__), '--elevhub-resolver']
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, creationflags=flags)
    except TypeError:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL)
    try:
        output, _ = process.communicate(json.dumps(config).encode('utf-8'), timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise TimeoutError('Elevhub DNS-opslag overskred tidsgrænsen.')
    if process.returncode != 0:
        raise OSError('Elevhub DNS-opslag fejlede.')
    result = json.loads(output)
    if not result.get('ok'):
        raise OSError('Elevhub DNS-opslag fejlede.')
    return result['address']


def hent_offentlig_json(hub, sti, *, usikker_test=False, data=None, timeout=4, ca_fil=''):
    """Legacy test-only request uden credentials; HTTPS kan aldrig bruge denne vej."""
    url = _url(hub, sti, usikker_test)
    body = None if data is None else json.dumps(data, ensure_ascii=False).encode('utf-8')
    payload = _json_request(url, {'Accept': 'application/json',
                                  **({'Content-Type': 'application/json'} if body is not None else {})},
                           body, timeout, ca_fil)
    if len(payload) > 65536:
        raise ValueError('Elevhub-svaret er for stort.')
    try:
        return json.loads(payload)
    except (TypeError, ValueError) as fejl:
        raise ValueError('Elevhub sendte ikke JSON.') from fejl


def hent_json(hub, sti, *, token, computer, ca_fil, usikker_test=False,
              data=None, timeout=4):
    """Send én authenticated request og decode JSON.

    Redirects afvises også hvis serveren forsøger at blive på samme host, så et
    token aldrig kan ende på en anden URL som følge af serverkonfiguration.
    """
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        raise ValueError('Elevhub-token mangler.')
    if not isinstance(computer, str) or not _COMPUTER.fullmatch(computer):
        raise ValueError('Elevhub-computernavn mangler.')
    url = _url(hub, sti, usikker_test)
    body = None if data is None else json.dumps(data, ensure_ascii=False).encode('utf-8')
    headers = {
        'Authorization': f'Bearer {token}',
        'X-Elevhub-Computer': computer,
        'Accept': 'application/json',
    }
    if body is not None:
        headers['Content-Type'] = 'application/json'
    payload = _json_request(url, headers, body, timeout, ca_fil)
    if len(payload) > 65536:
        raise ValueError('Elevhub-svaret er for stort.')
    try:
        return json.loads(payload)
    except (TypeError, ValueError) as fejl:
        raise ValueError('Elevhub sendte ikke JSON.') from fejl


def _worker_entry(argument):
    try:
        config = json.load(sys.stdin)
        if argument == '--elevhub-worker':
            result = _worker_json(config)
        else:
            result = {'ok': True, 'address': __import__('socket').gethostbyname(config['host'])}
        sys.stdout.write(json.dumps(result))
    except Exception as fejl:
        sys.stdout.write(json.dumps({'ok': False, 'reason': type(fejl).__name__}))
        raise SystemExit(1)


if __name__ == '__main__' and len(sys.argv) == 2 and sys.argv[1] in ('--elevhub-worker', '--elevhub-resolver'):
    _worker_entry(sys.argv[1])
