"""Same-origin return to the fixed installed kiosk page and its public assets."""
import json
import mimetypes
from pathlib import Path
import re
from urllib.parse import unquote,urlsplit
from student_appearance import KIOSK_CONFIG, _bytes

START_FILE=Path('/usr/local/share/elevhub/start.html')
ASSET_NAME=re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,160}\.(?:css|js|png|webp|svg|ttf|woff2)')


def home_csp(config_path):
    remote=''
    try:
        value=json.loads(_bytes(Path(config_path),65536))
        url=urlsplit(value.get('hub',''))
        if ((url.scheme=='https' or (url.scheme=='http' and url.hostname=='127.0.0.1')) and re.fullmatch(r'[a-zA-Z0-9.-]+',url.hostname or '')
                and not url.username and not url.password and url.path in ('','/')
                and not url.query and not url.fragment and (url.port is None or 1<=url.port<=65535)):
            remote=f' {url.scheme}://{url.hostname}'+(f':{url.port}' if url.port else '')
    except (OSError,ValueError,AttributeError,TypeError):
        pass
    return ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self'; connect-src 'self' http://127.0.0.1:8790"+remote+
            "; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'")


def serve_home(handler,path):
    if path!='/home' and not path.startswith('/assets/'):
        return False
    start=Path(getattr(handler.server,'kiosk_start_file',START_FILE))
    try:
        if path=='/home':
            data=_bytes(start,4*1024*1024)
            handler.svar(200,data,'text/html; charset=utf-8',{'Content-Security-Policy':home_csp(getattr(handler.server,'kiosk_config',KIOSK_CONFIG))})
        else:
            name=unquote(path[len('/assets/'):])
            if not ASSET_NAME.fullmatch(name):
                raise ValueError('invalid_asset')
            target=start.parent/'assets'/name
            data=_bytes(target,16*1024*1024)
            handler.svar(200,data,mimetypes.guess_type(name)[0] or 'application/octet-stream')
    except (OSError,ValueError):
        handler.svar(404,{'error':'Forsiden eller dens filer mangler. Brug Til start i elevbjælken.'})
    return True
