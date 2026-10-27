"""Valider værkstedernes konfiguration før serveren åbner porte."""
import re
from pathlib import Path
from urllib.parse import urlsplit

APP_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,31}')
DOMAENE = re.compile(r'(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}')
TYPER = {'vaerksted', 'browser', 'web', 'lokal', 'native'}
NATIVE_APPS = {'word', 'cadoodle'}


def https_url(url):
    if not isinstance(url, str) or any(ord(c) < 33 for c in url):
        raise ValueError('Værkstedets adresse skal være en HTTPS-adresse uden mellemrum.')
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.port not in (None, 443)):
        raise ValueError('Eksterne værksteder skal bruge HTTPS på port 443 uden login i URL.')
    return parsed


def valider(konf, regelsaet):
    if not isinstance(konf, dict) or not isinstance(konf.get('apps'), list) or not 1 <= len(konf['apps']) <= 32:
        raise ValueError('Konfigurationen skal indeholde 1–32 apps.')
    porte, navne = set(), set()
    for felt in ('hub_port', 'laerer_port'):
        port = konf.get(felt)
        if type(port) is not int or not 1 <= port <= 65535 or port in porte:
            raise ValueError('Serverportene skal være gyldige og forskellige.')
        porte.add(port)
    for app in konf['apps']:
        if not isinstance(app, dict) or not isinstance(app.get('id'), str) or not APP_ID.fullmatch(app['id']):
            raise ValueError('Ugyldigt værksteds-id.')
        if app['id'] in navne:
            raise ValueError('Værksteds-id må ikke gentages.')
        navne.add(app['id'])
        type_ = app.get('type', 'vaerksted')
        if type_ not in TYPER or not isinstance(app.get('navn'), str) or not app['navn'].strip():
            raise ValueError('Værkstedet mangler et navn eller har ukendt type.')
        for felt in ('standard_aaben', 'luk_ved_genstart'):
            if felt in app and type(app[felt]) is not bool:
                raise ValueError(f'{felt} skal være true eller false.')
        if 'lokal_service' in app and type(app['lokal_service']) is not bool:
            raise ValueError('lokal_service skal være true eller false.')
        if app.get('lokal_service') is True and type_ != 'lokal':
            raise ValueError('lokal_service må kun bruges af lokale værksteder.')
        if type_ == 'native':
            if app['id'] not in NATIVE_APPS or app.get('native_app') != app['id']:
                raise ValueError('Native apps må kun være Word eller CaDoodle.')
            if any(felt in app for felt in ('start_url', 'port', 'bagved', 'regler', 'kommando')):
                raise ValueError('Native apps må ikke have web- eller backendfelter.')
            continue
        if type_ in ('web', 'browser'):
            url = https_url(app.get('start_url'))
            if type_ == 'web':
                domaener = app.get('domaener')
                if (not isinstance(domaener, list) or not 1 <= len(domaener) <= 64
                        or any(not isinstance(d, str) or not DOMAENE.fullmatch(d) for d in domaener)
                        or len(set(domaener)) != len(domaener) or url.hostname not in domaener):
                    raise ValueError('Webværkstedet kræver præcise domæner, inklusive startadressens domæne.')
                for d in domaener:
                    if d.endswith(('.localhost', '.local', '.internal')):
                        raise ValueError('Et eksternt værksted må ikke åbne lokale navne.')
        else:
            port = app.get('port')
            if type(port) is not int or not 1 <= port <= 65535 or port in porte:
                raise ValueError('Værkstedernes porte skal være gyldige og forskellige.')
            porte.add(port)
            if type_ == 'vaerksted' and app.get('regler') not in regelsaet:
                raise ValueError(f"Ukendt regelsæt for {app['id']}.")
            if type_ == 'lokal' and app.get('mappe') is not None and not isinstance(app['mappe'], str):
                raise ValueError('Det lokale værksteds mappe skal være en sti.')
            if type_ == 'lokal' and app.get('lokal_service') is True:
                # These apps are served by the pupil computer's loopback
                # service.  Their assets deliberately do not belong in the
                # central hub configuration or on the teacher server.
                local_port = app.get('lokal_port', port)
                if type(local_port) is not int or local_port != port:
                    raise ValueError('lokal_port skal matche det lokale værksteds port.')
    return konf


def klargoer_lokale_apps(konf, basis):
    for app in konf['apps']:
        if app.get('type') != 'lokal':
            continue
        if app.get('lokal_service') is True:
            # Readiness is owned by the local elevhub service and its
            # installed asset root.  Do not require a central static folder.
            app['mappe'] = None
            app['klar'] = True
            continue
        mappe = app.get('mappe')
        rod = (Path(basis) / mappe).resolve() if mappe else None
        app['mappe'] = str(rod) if rod else None
        app['klar'] = bool(rod and (rod / 'index.html').is_file())
