"""Pupil-only projection of the existing private appearance format.

The release supplies school_appearance.py beside this module from app/;
source checkouts use that same canonical reader. No school asset is bundled.
"""
import base64
import json
import os
from pathlib import Path
import sys

try:
    from school_appearance import _manifest, _bytes, brand_colours
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.school_appearance import _manifest, _bytes, brand_colours

KIOSK_CONFIG = Path('/etc/elevhub/kiosk.json')


def read_appearance(config_path=KIOSK_CONFIG, *, config=None, verify=None):
    path = Path(config_path)
    if config is None:
        try:
            config = json.loads(_bytes(path, 65536))
        except (OSError, ValueError, UnicodeError):
            config = {}
    if not isinstance(config, dict):
        config = {}
    name = config.get('school_name', '')
    if not isinstance(name, str) or len(name)>120 or any(ord(c)<32 or ord(c)==127 for c in name):
        name = ''
    theme = config.get('theme', 'red')
    result = {'theme':theme if theme in ('red','green','yellow') else 'red',
              'school_name':name, 'brand':'', 'brand_dark':'', 'logo_data':'', 'font':'standard'}
    result.update(brand_colours(config))
    base = path.parent / 'local-appearance'
    # Only inspect this profile, never a host profile while testing another root.
    if not os.path.lexists(base):
        return result
    if verify:
        verify(base, directory=True)
        verify(base/'manifest.json')
    local = _manifest(path.parent)
    if local is None:
        raise ValueError('Skolens lokale udseende kunne ikke læses. Kontakt administratoren.')
    if verify:
        for key in ('_logo', '_website_css'):
            if local.get(key):verify(local[key][0])
        for selected in local.get('_static', {}).values():verify(selected[0])
    result.update({key:local[key] for key in ('theme','school_name','brand','brand_dark','font') if key in local})
    if local.get('_logo'):
        import mimetypes
        filename,content = local['_logo']
        mime = mimetypes.guess_type(filename.name)[0]
        result['logo_data'] = 'data:'+mime+';base64,'+base64.b64encode(content).decode('ascii')
    return result
