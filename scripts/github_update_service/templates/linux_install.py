#!/usr/bin/env python3
"""Root-only next-boot transaction. Klostermarksskolen, Makerværkstedet.

Never runs onboarding/clean-install roles. Snapshots managed software/config
before applying the kiosk/cadoodle roles; an interrupted apply is rolled back.
"""
from __future__ import annotations
import argparse
from contextlib import closing, contextmanager
import hashlib
import importlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import sqlite3
import stat
import subprocess
import tarfile
import tempfile
import threading
import time
import uuid

RELEASE='UNBOUND_RELEASE'
OPERATION='UNBOUND_OPERATION'
PACKAGE_SHA='UNBOUND_PACKAGE_SHA'
STATE='/var/lib/elevhub/updates/'+OPERATION
STATUS_PATH='/run/elevhub-update-status.json'
APPROVAL_PATH='/var/lib/elevhub/local-update/approval.json'
SERVICE='elevhub-full-update.service'
NAMES=[] # BOUND_LOCAL_TARGETS
ADMIN_UPDATE_FILES={
    'linux-elevhub/ansible/roles/adminkonto/tasks/update-interface.yml',
    'linux-elevhub/ansible/roles/adminkonto/files/elevhub-local-update.py',
    'linux-elevhub/ansible/roles/adminkonto/files/elevhub-local-update-gui.py',
    'linux-elevhub/ansible/roles/adminkonto/files/elevhub-admin-xsession.sh',
    'linux-elevhub/ansible/roles/adminkonto/files/elevhub-admin-session.sh',
    'linux-elevhub/ansible/roles/adminkonto/files/elevhub-admin-return.py',
    'linux-elevhub/ansible/roles/adminkonto/templates/admin-bash-profile.j2',
}
SHA=re.compile(r'[a-f0-9]{64}')
STAGE_RE=re.compile(r'UNBOUND_STAGE_PATTERN')
BACKUP_ROOTS=['/etc','/usr/local','/opt/cadoodle','/boot/grub','/usr/share/plymouth/themes/elevhub',
    '/var/cache/elevhub','/var/lib/elevhub/plymouth-initramfs.ok','/home/elev/bin',
    '/home/elev/.pki','/home/elev/.config/gtk-3.0','/home/elev/.hushlogin']

def at(root,path): return Path(root)/path.lstrip('/')

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): h.update(block)
    return h.hexdigest()

def atomic_text(path,value,mode=0o600):
    path=Path(path); temp=path.with_name('.'+path.name+'.pending')
    if path.is_symlink() or temp.is_symlink() or any(p.is_symlink() for p in path.parents): raise ValueError('unsafe atomic path')
    with temp.open('w',encoding='utf-8') as stream:
        os.chmod(temp,mode); stream.write(value); stream.flush(); os.fsync(stream.fileno())
    os.replace(temp,path)
    if os.name=='posix':
        directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)

def atomic_json(path,value,mode=0o600):
    atomic_text(path,json.dumps(value,ensure_ascii=False,indent=2)+'\n',mode)

def regular(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file(): raise ValueError('required regular file missing or linked: '+path.name)
    if any(p.is_symlink() for p in path.parents): raise ValueError('linked parent forbidden')
    return path

def secure_state(root):
    path=at(root,STATE)
    for parent in [*reversed(path.parents),path]:
        if not parent.exists(): parent.mkdir(mode=0o700)
        if parent.is_symlink() or not parent.is_dir(): raise ValueError('unsafe update directory')
        if parent.stat().st_uid!=0 or parent.stat().st_mode & 0o022:
            # /tmp is an ancestor of isolated tests, never of the live state.
            if not (Path(root)!=Path('/') and parent in Path(root).parents):
                raise ValueError('update state must have root-owned protected parents')
    path.chmod(0o700)
    return path

@contextmanager
def lock(state):
    import fcntl
    target=state/'lock'
    if target.is_symlink(): raise ValueError('unsafe lock')
    with target.open('a+b') as stream:
        os.chmod(target,0o600); fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield

def read_json(path): return json.loads(regular(path).read_text(encoding='utf-8'))

def managed_roots(root):
    return BACKUP_ROOTS+['/boot/'+p.name for p in at(root,'/boot').glob('initrd.img-*')
        if re.fullmatch(r'initrd\.img-[A-Za-z0-9.+_-]+',p.name)]

def profile(root,expected,check_hostname=True):
    if expected not in NAMES: raise ValueError('target outside school scope')
    if check_hostname and socket.gethostname()!=expected: raise ValueError('hostname identity mismatch')
    kiosk=read_json(at(root,'/etc/elevhub/kiosk.json')); device=read_json(at(root,'/etc/elevhub/enhed.json'))
    if kiosk.get('computer')!=expected or device.get('computer')!=expected: raise ValueError('device identity mismatch')
    if any(not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9_-]{43,128}',v) for v in [device.get('token'),kiosk.get('ui_token')]):
        raise ValueError('profile_missing: insufficient enrolled credentials')
    if device['token']==kiosk['ui_token']: raise ValueError('identity token collision')
    ipaddress.IPv4Address(kiosk['server'])
    if kiosk.get('protokol')!='https' or kiosk.get('usikker_test') is not False or kiosk.get('elev','elev')!='elev':
        raise ValueError('profile_missing: secure standard pupil profile required')
    ca=regular(at(root,'/etc/elevhub/ca.pem')).read_text()
    if '-----BEGIN CERTIFICATE-----' not in ca or 'PRIVATE KEY' in ca: raise ValueError('profile_missing: public school CA missing')
    config=at(root,'/etc/elevhub-install')
    if (config/'device.json').exists():
        stored=read_json(config/'device.json')
        if any(stored.get(k)!=v for k,v in {'computer':expected,'token':device['token'],'ui_token':kiosk['ui_token']}.items()):
            raise ValueError('stored/live enrolled identity mismatch')
    if (config/'settings.json').exists():
        settings=read_json(config/'settings.json')
        if settings.get('computer')!=expected or settings.get('server')!=kiosk['server']: raise ValueError('stored/live school profile mismatch')
    if (config/'ca.crt').exists() and regular(config/'ca.crt').read_bytes().strip()!=ca.encode().strip(): raise ValueError('stored/live CA mismatch')
    previous=read_json(config/'variables.json') if (config/'variables.json').exists() else {}
    if not isinstance(previous,dict): raise ValueError('profile_missing: invalid variables')
    paths={'kiosk_bruger':'elev','kiosk_startside_mappe':'/usr/local/share/elevhub',
        'kiosk_lokale_apps_root':'/usr/local/share/elevhub/apps','kiosk_lokale_projects':'/home/elev/Projekter',
        'kiosk_lokale_state':'/home/elev/Projekter/.elevhub','kiosk_lokal_package_dest':'/usr/local/lib/elevhub/projekter',
        'kiosk_lokal_service_dest':'/usr/local/lib/elevhub/projekter/lokal.py','kiosk_lokal_design_dest':'/usr/local/lib/elevhub/designvaerksted',
        'cadoodle_deb_sti':'/var/cache/elevhub/CaDoodle-1.9.6-amd64.deb',
        'cadoodle_runtime_archive':'/opt/cadoodle/lib/app/zulu25.36.15-ca-fx-jdk25.0.4-linux_x64.tar.gz'}
    for key,value in paths.items():
        if key in previous and previous[key]!=value: raise ValueError('profile_missing: nonstandard managed path '+key)
    return kiosk,device,previous


def appearance_preflight(root):
    """Validate local appearance before the updater snapshots and migrates it."""
    local=at(root,'/etc/elevhub/local-appearance')
    if local.is_symlink(): raise ValueError('local_appearance_requires_migration_before_update')
    if local.exists():
        manifest=local/'manifest.json'
        try:
            if manifest.stat().st_size>65536 or manifest.stat().st_uid!=0 or local.stat().st_uid!=0 or local.stat().st_mode&0o022: raise ValueError
            value=json.loads(regular(manifest).read_text(encoding='utf-8'))
            if not isinstance(value,dict) or value.get('schemaVersion')!=1 or not isinstance(value.get('school_name'),str) or value.get('theme') not in ('red','yellow','green'):
                raise ValueError
            style=value.get('style',{})
            if not isinstance(style,dict) or set(style)-{'website_css','website_css_sha256','logo','logo_sha256','static'}: raise ValueError
            for key in ('website_css','logo'):
                if key not in style: continue
                relative=style[key]
                if not isinstance(relative,str) or not relative or Path(relative).is_absolute() or '..' in Path(relative).parts: raise ValueError
                file=regular(local/relative)
                limit=262144 if key=='website_css' else 4*1024*1024
                if file.stat().st_uid!=0 or file.stat().st_mode&0o022 or file.stat().st_size>limit or hashlib.sha256(file.read_bytes()).hexdigest()!=style.get(key+'_sha256'): raise ValueError
            static=style.get('static',{})
            if not isinstance(static,dict) or len(static)>128: raise ValueError
            for url,descriptor in static.items():
                if not isinstance(url,str) or not url.startswith('/') or not isinstance(descriptor,dict) or set(descriptor)!={'path','sha256'}: raise ValueError
                relative=descriptor['path']
                if not isinstance(relative,str) or not relative or Path(relative).is_absolute() or '..' in Path(relative).parts: raise ValueError
                file=regular(local/relative)
                if file.suffix.lower() not in ('.css','.png','.jpg','.jpeg','.webp','.svg','.ico','.woff','.woff2','.ttf','.otf'): raise ValueError
                if file.stat().st_uid!=0 or file.stat().st_mode&0o022 or file.stat().st_size>(262144 if file.suffix.lower()=='.css' else 4*1024*1024) or not re.fullmatch(r'[a-f0-9]{64}',descriptor['sha256']) or hashlib.sha256(file.read_bytes()).hexdigest()!=descriptor['sha256']: raise ValueError
            for item in local.rglob('*'):
                if item.is_symlink() or item.stat().st_uid!=0 or item.stat().st_mode&0o022: raise ValueError
        except (OSError,ValueError,TypeError,KeyError,json.JSONDecodeError):
            raise ValueError('local_appearance_requires_migration_before_update') from None
    web=at(root,'/usr/local/share/elevhub')
    logo=web/'assets/klostermarksskolen-logo.png'
    for relative,markers in [
            ('start.html',('klostermarksskolen-logo','klostermarksskolen / værkstedet','klostermarksskolen / vaerkstedet')),
            ('assets/creative-hub.css',('#b4121a','#920008'))]:
        path=web/relative
        if not path.exists() and not path.is_symlink(): continue
        try:
            file=regular(path)
            if file.stat().st_size>2*1024*1024:
                raise ValueError('appearance file too large')
            content=file.read_text(encoding='utf-8',errors='replace').casefold()
        except (OSError,ValueError):
            raise ValueError('local_appearance_requires_migration_before_update') from None
        if any(marker in content for marker in markers):
            continue
        if relative=='start.html' and 'hub-bauhaus-v1.png' not in content and not _legacy_appearance_detected(root) and not any(marker in content for marker in markers):
            raise ValueError('local_appearance_requires_migration_before_update')

_APPEARANCE_ASSET_SUFFIXES={'.css','.png','.jpg','.jpeg','.webp','.svg','.ico','.woff','.woff2','.ttf','.otf'}
_APPEARANCE_OVERLAY_FILES={
    '/usr/local/share/elevhub/start.html',
    '/usr/local/share/elevhub/assets/creative-hub.css',
    '/usr/local/share/elevhub/assets/local-school.css',
    '/usr/local/share/elevhub/assets/creative-hub.js',
    '/usr/local/share/elevhub/assets/workshops.js',
    '/usr/local/lib/elevhub/elevbjaelke.py',
    '/usr/local/lib/elevhub/startup_screen.py',
}

def _appearance_asset(value):
    value=value.split('?',1)[0].split('#',1)[0]
    if value.startswith('assets/'): value=value[7:]
    path=PurePosixPath(value)
    if not value or path.is_absolute() or '..' in path.parts or len(path.parts)!=1: return None
    return path.name if path.suffix.lower() in _APPEARANCE_ASSET_SUFFIXES or path.name.casefold()=='metrophobic-ofl.txt' else None

def _legacy_appearance_detected(root):
    web=at(root,'/usr/local/share/elevhub'); start=web/'start.html'; css=web/'assets/creative-hub.css'
    texts=[]
    for path in (start,css,web/'assets/creative-hub.js',web/'assets/workshops.js'):
        if path.is_file(): texts.append(path.read_text(encoding='utf-8',errors='replace').casefold())
    if any(marker in text for text in texts for marker in (
            'klostermarksskolen-logo','klostermarksskolen / værkstedet','klostermarksskolen / vaerkstedet','#b4121a','#920008')):
        return True
    if start.is_file() and texts and (web/'assets/klostermarksskolen-logo.png').is_file() and 'logo' in texts[0]: return True
    for text in texts:
        for value in re.findall(r'''assets/[^"'\s>]+''',text):
            name=Path(value.split('?',1)[0]).name.casefold()
            if name.endswith('.css') and name not in ('creative-hub.css','local-school.css'): return True
            if name and name.endswith(('.png','.jpg','.jpeg','.webp','.svg','.ico')) and name!='hub-bauhaus-v1.png' and not re.fullmatch(r'workshop-[a-z0-9-]+-bauhaus-v1\.png',name): return True
    return False

def _valid_appearance_entry(name):
    if not isinstance(name,str) or not name.startswith('/') or '\\' in name: return False
    path=PurePosixPath(name)
    return '..' not in path.parts and str(path)==name.rstrip('/') and (
        name=='/etc/elevhub/local-appearance' or name.startswith('/usr/local/share/elevhub/') or
        name in ('/usr/local/lib/elevhub/elevbjaelke.py','/usr/local/lib/elevhub/startup_screen.py'))

def _js_workshop_images(text):
    pattern=r'''(?:(['"])([^'"]+)\1|([A-Za-z][A-Za-z0-9_-]*))\s*:\s*\{[^\n{}]*?\bimage\s*:\s*(['"])([^'"]+)\4'''
    return {match[2] or match[3]:match[5] for match in re.finditer(pattern,text)}

def _js_images(text):
    return [match[2] for match in re.finditer(r'''\bimage\s*:\s*(['"])([^'"]+)\1''',text)]

def _appearance_fragments(text):
    patterns={
        'brand':r'(<a\b(?=[^>]*\bclass=["\'][^"\']*\bbrand\b)[^>]*>.*?</a>)',
        'eyebrow':r'(<p\b(?=[^>]*\bclass=["\'][^"\']*\beyebrow\b)[^>]*>.*?</p>)',
        'hero_image':r'(<figure\b(?=[^>]*\bclass=["\'][^"\']*\bhero-photo\b)[^>]*>\s*<img\b[^>]*>)',
        'footer':r'(<footer\b(?=[^>]*\bclass=["\'][^"\']*\bhub-footer\b)[^>]*>.*?</footer>)',
    }
    return {name:match.group(1) for name,pattern in patterns.items()
            if (match:=re.search(pattern,text,re.I|re.S))}

def _appearance_names(root):
    web=at(root,'/usr/local/share/elevhub'); legacy=_legacy_appearance_detected(root); names=[]
    for name in ['/usr/local/share/elevhub/start.html','/usr/local/share/elevhub/assets/creative-hub.css','/usr/local/share/elevhub/assets/local-school.css']:
        if at(root,name).is_file() and legacy: names.append(name)
    if not names: return names,{}
    metadata={'fragments':{},'workshops':{},'creative_images':[],'assets':[],'native':[]}
    start=at(root,'/usr/local/share/elevhub/start.html')
    if start.is_file():
        text=start.read_text(encoding='utf-8',errors='replace'); metadata['fragments']=_appearance_fragments(text)
        for ref in re.findall(r'''(?:src|href)\s*=\s*["']assets/([^"']+)["']''',text):
            asset=_appearance_asset(ref)
            if asset: metadata['assets'].append(asset)
    for filename,key in [('workshops.js','workshops'),('creative-hub.js','creative_images')]:
        path=web/'assets'/filename
        if not path.is_file(): continue
        text=path.read_text(encoding='utf-8',errors='replace')
        metadata[key]=_js_workshop_images(text) if key=='workshops' else _js_images(text)
        for ref in (_js_images(text) if key=='creative_images' else metadata[key].values()):
            asset=_appearance_asset(ref)
            if asset: metadata['assets'].append(asset)
    for path in (web/'assets').glob('*'):
        if path.is_file() and (path.suffix.lower() in _APPEARANCE_ASSET_SUFFIXES or path.name.casefold()=='metrophobic-ofl.txt') and any(token in path.name.casefold() for token in ('logo','favicon','font','metrophobic')):
            metadata['assets'].append(path.name)
    for name in ['/usr/local/lib/elevhub/elevbjaelke.py','/usr/local/lib/elevhub/startup_screen.py']:
        if at(root,name).is_file(): metadata['native'].append(name)
    metadata['assets']=list(dict.fromkeys(metadata['assets']))
    names.extend('/usr/local/share/elevhub/assets/'+asset for asset in metadata['assets'] if (web/'assets'/asset).is_file())
    names.extend(name for name in _APPEARANCE_OVERLAY_FILES if at(root,name).is_file() and name not in names)
    return list(dict.fromkeys(names)),metadata

def snapshot_appearance(root,state):
    destination=state/'appearance-backup'
    if destination.exists():
        if destination.is_symlink(): raise ValueError('unsafe appearance snapshot')
        shutil.rmtree(destination)
    destination.mkdir(mode=0o700); entries=[]; names=[]; metadata={}
    local=at(root,'/etc/elevhub/local-appearance')
    if local.exists(): names.append('/etc/elevhub/local-appearance')
    legacy_names,metadata=_appearance_names(root); names.extend(legacy_names); inventory={}
    for name in dict.fromkeys(names):
        source=at(root,name)
        if not source.exists() and not source.is_symlink(): continue
        if source.is_symlink(): raise ValueError('local_appearance_requires_migration_before_update')
        target=destination/name.lstrip('/'); target.parent.mkdir(parents=True,exist_ok=True)
        copy_owned(source,target); entries.append(name); inventory[name]=tree(source) if source.is_dir() else digest(source)
    atomic_json(state/'appearance.json',{'schema':2,'entries':entries,'inventory':inventory,'overlay':metadata})

def _write_appearance_text(path,text):
    path=Path(path); regular(path); temporary=path.with_name('.'+path.name+'.appearance-'+uuid.uuid4().hex)
    temporary.write_text(text,encoding='utf-8'); shutil.copystat(path,temporary); st=path.stat(); os.chown(temporary,st.st_uid,st.st_gid)
    os.replace(temporary,path); _fsync_parent(path)

def _replace_appearance_fragments(candidate,fragments):
    patterns={
        'brand':r'<a\b(?=[^>]*\bclass=["\'][^"\']*\bbrand\b)[^>]*>.*?</a>',
        'eyebrow':r'<p\b(?=[^>]*\bclass=["\'][^"\']*\beyebrow\b)[^>]*>.*?</p>',
        'hero_image':r'<figure\b(?=[^>]*\bclass=["\'][^"\']*\bhero-photo\b)[^>]*>\s*<img\b[^>]*>',
        'footer':r'<footer\b(?=[^>]*\bclass=["\'][^"\']*\bhub-footer\b)[^>]*>.*?</footer>',
    }
    result=candidate
    for name,replacement in fragments.items():
        if name not in patterns: continue
        result,count=re.subn(patterns[name],lambda _match:replacement,result,count=1,flags=re.I|re.S)
        if count!=1: raise ValueError('appearance migration target missing: '+name)
    return result.replace('<body data-theme="red">','<body>').replace('<header class="hub-header" data-theme="red">','<header class="hub-header">')

def _normalise_document_title(text):
    obsolete='Værkstedet · Elevværkstedet'; current='Værkstedet · Makerværkstedet'
    return re.sub(r'''(document\.title\s*=\s*)(['"])'''+re.escape(obsolete)+r'''\2''',lambda match:match.group(1)+match.group(2)+current+match.group(2),text)

def _native_brand_colors(text):
    values={}
    for line in text.splitlines():
        if '=' not in line: continue
        names,colors=line.split('=',1); variables=re.findall(r'\b(BRAND(?:_MOERK|_DARK)?)\b',names); literals=re.findall(r'#[0-9a-fA-F]{6}',colors)
        values.update(dict(zip(variables,literals)))
    return values

def _patch_native_appearance(source_text,candidate_text):
    old_colors=_native_brand_colors(source_text); new_colors=_native_brand_colors(candidate_text); result=candidate_text
    for name,original in old_colors.items():
        replacement=new_colors.get(name)
        if replacement is not None and replacement!=original: result=result.replace(replacement,original)
    return result

def restore_appearance(root,state):
    receipt=read_json(state/'appearance.json')
    if receipt.get('schema') not in (1,2) or not isinstance(receipt.get('entries'),list) or not isinstance(receipt.get('inventory'),dict): raise ValueError('appearance migration receipt invalid')
    if any(not _valid_appearance_entry(name) for name in receipt['entries']): raise ValueError('appearance migration receipt invalid')
    sources=state/'appearance-backup'; overrides={}
    for name in receipt['entries']:
        source=sources/name.lstrip('/')
        if not source.exists() or source.is_symlink(): raise ValueError('appearance migration backup incomplete')
        if receipt['inventory'].get(name)!=(tree(source) if source.is_dir() else digest(source)): raise ValueError('appearance migration backup corrupt')
    if receipt.get('schema')==1:
        for name in receipt['entries']:
            source=sources/name.lstrip('/'); target=at(root,name); target.parent.mkdir(parents=True,exist_ok=True); _restore_entry(source,target)
        return {'overrides':overrides}
    overlay=receipt.get('overlay',{})
    if not isinstance(overlay,dict) or not isinstance(overlay.get('fragments',{}),dict) or not isinstance(overlay.get('native',[]),list): raise ValueError('appearance migration receipt invalid')
    if any(not isinstance(value,str) for value in overlay.get('fragments',{}).values()) or any(not _valid_appearance_entry(name) for name in overlay.get('native',[])): raise ValueError('appearance migration receipt invalid')
    def saved(name): return sources/name.lstrip('/')
    names=set(receipt['entries'])
    skip={'/etc/elevhub/local-appearance','/usr/local/share/elevhub/assets/creative-hub.css','/usr/local/share/elevhub/assets/local-school.css','/usr/local/share/elevhub/assets/workshops.js','/usr/local/share/elevhub/assets/creative-hub.js','/usr/local/share/elevhub/start.html','/usr/local/lib/elevhub/elevbjaelke.py','/usr/local/lib/elevhub/startup_screen.py'}
    for name in receipt['entries']:
        if name in skip: continue
        source=saved(name); target=at(root,name); target.parent.mkdir(parents=True,exist_ok=True); _restore_entry(source,target)
        overrides[name]=tree(target) if target.is_dir() else digest(target)
    css='/usr/local/share/elevhub/assets/creative-hub.css'; local_css='/usr/local/share/elevhub/assets/local-school.css'
    if local_css in names:
        _restore_entry(saved(local_css),at(root,local_css)); overrides[local_css]=digest(at(root,local_css))
    elif css in names:
        _restore_entry(saved(css),at(root,local_css)); overrides[local_css]=digest(at(root,local_css))
    start='/usr/local/share/elevhub/start.html'
    if start in names:
        target=at(root,start); saved_start=saved(start).read_text(encoding='utf-8',errors='replace')
        fragments=overlay.get('fragments',{})
        # Very old Kloster pages can consist only of the school logo.  There
        # is no safe fragment boundary in those pages, so retain that exact
        # legacy document; modern/generic pages still use fragment merging.
        if not fragments and 'klostermarksskolen-logo' in saved_start.casefold():
            candidate=saved_start
        else:
            candidate=_replace_appearance_fragments(regular(target).read_text(encoding='utf-8',errors='replace'),fragments)
        if css in names or local_css in names: candidate=re.sub(r'href="assets/(?:creative-hub|local-school)\.css"','href="assets/local-school.css"',candidate)
        _write_appearance_text(target,candidate); overrides[start]=digest(target)
    workshops='/usr/local/share/elevhub/assets/workshops.js'
    if workshops in names:
        old=_js_workshop_images(saved(workshops).read_text(encoding='utf-8',errors='replace')); target=at(root,workshops); candidate=regular(target).read_text(encoding='utf-8',errors='replace'); current=_js_workshop_images(candidate)
        for key,old_image in old.items():
            new_image=current.get(key)
            if new_image and new_image!=old_image: candidate=candidate.replace(new_image,old_image)
        _write_appearance_text(target,candidate); overrides[workshops]=digest(target)
    creative='/usr/local/share/elevhub/assets/creative-hub.js'
    if creative in names:
        old_text=saved(creative).read_text(encoding='utf-8',errors='replace'); target=at(root,creative); candidate=regular(target).read_text(encoding='utf-8',errors='replace')
        for new_image,old_image in zip(_js_images(candidate),_js_images(old_text)):
            if new_image!=old_image: candidate=candidate.replace(new_image,old_image)
        candidate=_normalise_document_title(candidate); _write_appearance_text(target,candidate); overrides[creative]=digest(target)
    for name in overlay.get('native',[]):
        if name not in names: continue
        target=at(root,name); candidate=regular(target).read_text(encoding='utf-8',errors='replace'); _write_appearance_text(target,_patch_native_appearance(saved(name).read_text(encoding='utf-8',errors='replace'),candidate)); overrides[name]=digest(target)
    local='/etc/elevhub/local-appearance'
    if local in names: _restore_entry(saved(local),at(root,local)); overrides[local]=tree(at(root,local))
    return {'overrides':overrides}

def safe_member(name):
    if not name or name.startswith('/') or '\\' in name or '..' in PurePosixPath(name).parts or ':' in name or str(PurePosixPath(name))!=name.rstrip('/'):
        raise ValueError('unsafe payload path')

def unpack(package,destination):
    seen=set(); manifest=None; actual={}
    with tarfile.open(package,'r|gz') as archive:
        for member in archive:
            safe_member(member.name); name=member.name.rstrip('/')
            if name in seen: raise ValueError('duplicate archive member')
            seen.add(name); target=destination/name
            if member.isdir(): target.mkdir(parents=True,exist_ok=True); continue
            if not member.isfile() or member.size>4*1024**3: raise ValueError('payload must contain only regular files/directories')
            target.parent.mkdir(parents=True,exist_ok=True)
            with archive.extractfile(member) as source,target.open('xb') as output:
                h=hashlib.sha256()
                for block in iter(lambda:source.read(1024*1024),b''): output.write(block); h.update(block)
            target.chmod((member.mode & 0o755)|0o400); actual[name]=h.hexdigest()
            if name=='manifest.json':
                if member.size>4*1024**2: raise ValueError('manifest too large')
                manifest=json.loads(target.read_text())
    if not isinstance(manifest,dict) or not isinstance(manifest.get('files'),dict): raise ValueError('payload manifest missing')
    expected=manifest['files']
    if set(actual)!=set(expected)|{'manifest.json'}: raise ValueError('payload inventory mismatch')
    for name,sha in expected.items():
        safe_member(name)
        if not isinstance(sha,str) or not SHA.fullmatch(sha) or actual.get(name)!=sha: raise ValueError('payload checksum mismatch: '+name)
    if read_json(destination/'linux-elevhub/server/elevhub/release.json').get('release_id')!=RELEASE:
        raise ValueError('payload software version mismatch')
    return manifest

def tree(root):
    result={}
    if not root.exists(): return result
    for directory,dirs,names in os.walk(root,followlinks=False):
        for name in sorted(dirs+names):
            path=Path(directory)/name; st=path.lstat()
            result[path.relative_to(root).as_posix()]={'mode':stat.S_IMODE(st.st_mode),'uid':st.st_uid,'gid':st.st_gid,
                'value':os.readlink(path) if path.is_symlink() else digest(path) if path.is_file() else 'directory'}
    return result


PROJECT_METADATA={'.elevhub/filindeks.json','.elevhub/projekter.sqlite3'}


def verify_project_metadata(root):
    """Check legacy live metadata without mistaking it for an unchanged snapshot."""
    state=root/'.elevhub'
    index=read_json(state/'filindeks.json')
    if not isinstance(index,dict): raise ValueError('project index invalid')
    database=regular(state/'projekter.sqlite3')
    try:
        with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as db:
            if db.execute('PRAGMA integrity_check').fetchone() != ('ok',):
                raise ValueError('project database corrupt')
            projects=db.execute('SELECT id, revision FROM projects').fetchall()
            versions=db.execute('SELECT project, revision, digest, size FROM versions').fetchall()
            if len({p[0] for p in projects})!=len(projects): raise ValueError('duplicate project metadata')
            maxima={p[0]:0 for p in projects}
            registered=set()
            for project,revision,sha,size in versions:
                if (not isinstance(project,str) or not re.fullmatch(r'[a-f0-9]{32}',project)
                        or project not in maxima or type(revision)!=int or revision<1
                        or not isinstance(sha,str) or not SHA.fullmatch(sha)
                        or type(size)!=int or size<1):
                    raise ValueError('project version metadata invalid')
                version=regular(state/'versioner'/project/(str(revision)+'.bin'))
                if version.stat().st_size!=size or digest(version)!=sha:
                    raise ValueError('project version content mismatch')
                registered.add((project,revision))
                maxima[project]=max(maxima[project],revision)
            if any(not isinstance(project,str) or not re.fullmatch(r'[a-f0-9]{32}',project)
                    or type(revision)!=int or revision!=maxima[project] for project,revision in projects):
                raise ValueError('project revision metadata invalid')
            for key,record in index.items():
                if (not isinstance(key,str) or not isinstance(record,dict)
                        or record.get('id') not in maxima
                        or type(record.get('revision'))!=int
                        or not 1<=record['revision']<=maxima[record['id']]
                        or (record['id'],record['revision']) not in registered):
                    raise ValueError('project index reference invalid')
            files=state/'versioner'
            if files.exists():
                for version in files.rglob('*.bin'):
                    relative=version.relative_to(files).parts
                    if (len(relative)!=2 or not re.fullmatch(r'[a-f0-9]{32}',relative[0])
                            or not re.fullmatch(r'[1-9][0-9]*\.bin',relative[1])
                            or (relative[0],int(relative[1][:-4])) not in registered):
                        raise ValueError('unregistered project version blob')
    except sqlite3.DatabaseError as error:
        raise ValueError('project database invalid') from error


def verify_projects(root,expected,*,legacy=False):
    actual=tree(root)
    if actual==expected: return 'byte_identical'
    if not legacy or not isinstance(expected,dict) or set(actual)!=set(expected):
        raise ValueError('projects changed')
    changed={name for name in actual if actual[name]!=expected[name]}
    if not changed or not changed<=PROJECT_METADATA:
        raise ValueError('projects changed')
    if any({key:value for key,value in actual[name].items() if key!='value'}!=
           {key:value for key,value in expected[name].items() if key!='value'}
           for name in changed): raise ValueError('project metadata ownership or mode changed')
    verify_project_metadata(root)
    return 'legacy_metadata_validated'


def stop_project_writer(run):
    run(['systemctl','stop','elevhub-lokal.service'],check=True,timeout=90)


def reload_dbus_after_restore(run):
    """Refresh account lookups after /etc was replaced, without stopping the bus."""
    active=run(['systemctl','is-active','--quiet','dbus.service'],check=False,timeout=15)
    if active.returncode!=0: return 'skipped_inactive'
    run(['systemctl','reload','dbus.service'],check=True,timeout=30)
    return 'reloaded'


class BootStatus:
    """Small fixed-message tty1 heartbeat; never displays logs or profile data."""
    STEPS={'checking':'Kontrollerer opdateringen', 'snapshot':'Sikrer eksisterende filer',
        'installing':'Installerer programmet', 'verifying':'Kontrollerer filer og version',
        'recovering':'Gendanner tidligere version', 'complete':'Opdateringen er færdig',
        'rolled_back':'Tidligere version er gendannet. Kontakt lærer.',
        'failed':'Opdateringen kunne ikke starte. Kontakt lærer.',
        'recovery_required':'Gendannelse kræver hjælp. Kontakt lærer.'}

    def __init__(self,root,run,stream=None,interval=10):
        self.root=Path(root)
        self.live=Path(root)==Path('/')
        self.run=run; self.stream=stream; self.interval=interval
        self.started=None; self.step='checking'; self.finished=threading.Event()
        self.thread=None; self.lock=threading.Lock()

    def start(self):
        if not self.live: return
        try:
            self.run(['plymouth','quit'],check=False,timeout=15,
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        except (OSError,subprocess.SubprocessError): pass
        if self.stream is None:
            try: self.stream=open('/dev/tty1','w',encoding='utf-8',errors='replace',buffering=1)
            except OSError: return
        self.started=time.monotonic(); self.render()
        publish_status(self.root, self.step, self.started)
        self.thread=threading.Thread(target=self._heartbeat,daemon=True); self.thread.start()

    def _heartbeat(self):
        while not self.finished.wait(self.interval): self.render()

    def phase(self,step):
        if step not in self.STEPS: raise ValueError('invalid status phase')
        self.step=step; self.render()
        publish_status(self.root, self.step, self.started)

    def render(self):
        if self.started is None: return
        elapsed=max(0,int(time.monotonic()-self.started))
        message=('\x1b[2J\x1b[H\nMakerværkstedet opdateres\n\n'
            'Lad computeren være tændt. Genstart ikke.\n\n'
            +self.STEPS[self.step]+f' — {elapsed // 60} min {elapsed % 60} sek\n')
        with self.lock:
            try: self.stream.write(message); self.stream.flush()
            except OSError: pass

    def close(self):
        self.finished.set()
        if self.thread is not None: self.thread.join(timeout=2)
        if self.stream is not None and self.live:
            try: self.stream.close()
            except OSError: pass

def publish_status(root, phase, started_at=None, status=None):
    """Best-effort public boot projection; never participates in the transaction."""
    statuses={'staged_awaiting_restart','applying','recovering','complete','failed_rolled_back',
        'failed_before_apply','recovery_required'}
    phases={'checking':'applying','snapshot':'applying','installing':'applying','verifying':'applying',
        'recovering':'recovering','rolled_back':'failed_rolled_back','failed':'failed_before_apply',
        'recovery_required':'recovery_required','complete':'complete'}
    status=status or phases.get(phase)
    if status not in statuses: return
    root=Path(root); boot_path=at(root,'/proc/sys/kernel/random/boot_id')
    try:
        boot_id=boot_path.read_text(encoding='ascii').strip()
        if not re.fullmatch(r'[0-9a-fA-F-]{36}',boot_id): return
        destination=at(root,STATUS_PATH); destination.parent.mkdir(parents=True,exist_ok=True)
        atomic_json(destination,{'schema':1,'status':status,'phase':phase,'release':RELEASE,
            'updated_at':time.time(),'boot_id':boot_id,**({'started_at':started_at} if started_at else {})},0o644)
    except (OSError,ValueError):
        return

def journal(state): return read_json(state/'journal.json') if (state/'journal.json').exists() else {}

def read_approval(root,expected):
    """Read the root-owned local-admin approval, failing closed on any drift."""
    path=at(root,APPROVAL_PATH); parent=path.parent
    try:
        parent_stat=parent.lstat(); file_stat=path.lstat()
        if (any(p.is_symlink() for p in path.parents) or
                not stat.S_ISDIR(parent_stat.st_mode) or parent_stat.st_uid!=0 or stat.S_IMODE(parent_stat.st_mode)!=0o700 or
                not stat.S_ISREG(file_stat.st_mode) or file_stat.st_uid!=0 or stat.S_IMODE(file_stat.st_mode)!=0o600):
            raise ValueError
        value=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError,TypeError,json.JSONDecodeError):
        raise ValueError('local_admin_required') from None
    if (not isinstance(value,dict) or set(value)!={'schema','computer','version','package_sha256','restart_authorized'} or
            value.get('schema')!=1 or value.get('computer')!=expected or value.get('version')!=RELEASE or
            not SHA.fullmatch(str(value.get('package_sha256',''))) or value.get('package_sha256')!=PACKAGE_SHA or
            type(value.get('restart_authorized')) is not bool):
        raise ValueError('local_admin_required')
    return value

def approval_state(root,expected):
    try: return read_approval(root,expected)
    except ValueError: return None

def controller_available(run,output=subprocess.DEVNULL):
    try:
        importlib.invalidate_caches(); importlib.import_module('yaml')
        executable=shutil.which('ansible-playbook')
        if not executable: return False
        run([executable,'--version'],stdout=output,stderr=subprocess.STDOUT,check=True,timeout=30)
        return True
    except (ImportError,OSError,subprocess.SubprocessError): return False

def ensure_controller(state,run):
    """Install only missing local controller dependencies, before arming boot.

    This deliberately runs during staging. It never upgrades the OS, removes a
    package, stops an application or creates a login dependency on failure.
    """
    with (state/'prerequisites.log').open('a') as output:
        os.chmod(state/'prerequisites.log',0o600)
        if controller_available(run,output): return
        env=os.environ.copy(); env.update(DEBIAN_FRONTEND='noninteractive',NEEDRESTART_MODE='l')
        try:
            run(['apt-get','-o','DPkg::Lock::Timeout=60','update'],env=env,stdout=output,stderr=subprocess.STDOUT,check=True,timeout=600)
            run(['apt-get','-o','DPkg::Lock::Timeout=60','--no-upgrade','--no-remove','--no-install-recommends','install','-y',
                'ansible-core','python3-yaml'],env=env,stdout=output,stderr=subprocess.STDOUT,check=True,timeout=900)
            if not controller_available(run,output): raise RuntimeError('controller unavailable after dependency installation')
        except Exception:
            raise ValueError('profile_missing: local update-controller prerequisites unavailable; inspect root prerequisites.log') from None

def dependency_path(root): return at(root,'/etc/systemd/system/getty@tty1.service.d/zz-full-update.conf')

def stage_durability_barrier(root):
    if Path(root)==Path('/'): os.sync()

def armed(root,expected):
    try:
        drop=dependency_path(root); unit=at(root,'/etc/systemd/system/'+SERVICE)
        return (regular(drop).read_text()=='[Unit]\nRequires='+SERVICE+'\nAfter='+SERVICE+'\n'
            and 'ExecStart=/usr/bin/python3 '+STATE+'/install.py --boot '+expected+'\n' in regular(unit).read_text())
    except (ValueError,OSError): return False

def disarm(root,run):
    # Remove only the dependency created by this operation. Other getty
    # overrides and all currently running pupil processes are left alone.
    drop=dependency_path(root)
    if drop.exists() or drop.is_symlink():
        if drop.is_symlink() or drop.read_text()!='[Unit]\nRequires='+SERVICE+'\nAfter='+SERVICE+'\n':
            raise ValueError('operation login dependency changed; requires administrator review')
        drop.unlink()
    run(['systemctl','daemon-reload'],check=True,timeout=30)

def disarm_terminal(root, state, run):
    """Disarm only after a verified terminal journal; retain evidence on failure."""
    try:
        disarm(root,run)
    except Exception as error:
        atomic_json(Path(state)/'disarm-failure.json',
            {'exception_type':type(error).__name__,'failed_at':time.time()})

def stage(root,expected,source,payload_sha,payload_size,installer_source,run=subprocess.run):
    if os.geteuid()!=0: raise ValueError('root required')
    if not SHA.fullmatch(payload_sha) or not SHA.fullmatch(PACKAGE_SHA) or type(payload_size)!=int: raise ValueError('invalid payload pin')
    read_approval(root,expected)
    appearance_preflight(root)
    state=secure_state(root)
    with lock(state):
        profile(root,expected); old=journal(state)
        if old.get('computer') not in (None,expected): raise ValueError('queue state identity mismatch')
        if old.get('status') in ('complete','applying','recovery_required','failed_rolled_back') or (
                old.get('status')=='staged_awaiting_restart' and armed(root,expected)):
            if old.get('package_sha256')!=PACKAGE_SHA: raise ValueError('local_admin_required')
            if old.get('status')=='staged_awaiting_restart': publish_status(root,'checking',status='staged_awaiting_restart')
            return {k:old[k] for k in ('status','release','computer') if k in old}
        if (state/'bundle').exists():
            # Only this operation's private, uncommitted staging tree is removed.
            # Applying/committed/recovery states returned above never reach here.
            if (state/'bundle').is_symlink(): raise ValueError('unsafe bundle directory')
            shutil.rmtree(state/'bundle')
        regular(source)
        with tempfile.TemporaryDirectory(prefix='stage-',dir=state) as temporary:
            temp=Path(temporary); archive=temp/'payload.tar.gz'; shutil.copyfile(source,archive)
            if archive.stat().st_size!=payload_size or digest(archive)!=payload_sha: raise ValueError('payload pin mismatch')
            bundle=temp/'bundle'; bundle.mkdir(mode=0o700); manifest=unpack(archive,bundle)
            size=0
            for name in managed_roots(root):
                p=at(root,name)
                if p.is_file(): size+=p.stat().st_size
                elif p.is_dir(): size+=sum(x.stat().st_size for x in p.rglob('*') if x.is_file() and not x.is_symlink())
            if shutil.disk_usage(state).free<size+512*1024**2: raise ValueError('insufficient rollback space')
            os.replace(bundle,state/'bundle')
        atomic_text(state/'install.py',installer_source)
        atomic_json(state/'pins.json',{'payload_sha256':payload_sha,'package_sha256':PACKAGE_SHA,'release':RELEASE,'manifest':manifest})
        value={'release':RELEASE,'operation':OPERATION,'package_sha256':PACKAGE_SHA,
            'status':'staged_awaiting_restart','computer':expected,'staged_at':time.time()}
        try: ensure_controller(state,run)
        except Exception:
            atomic_json(state/'journal.json',{**value,'status':'stage_retry','failure_code':'controller_prerequisites_unavailable'})
            raise
        unit=('[Unit]\nDescription=Makervaerkstedet queued pupil software update\nAfter=local-fs.target network-online.target\n'
            'Wants=network-online.target\nBefore=getty@tty1.service\n[Service]\nType=oneshot\nTimeoutStartSec=7200\n'
            'ExecStart=/usr/bin/python3 '+STATE+'/install.py --boot '+expected+'\n[Install]\nWantedBy=multi-user.target\n')
        unitfile=at(root,'/etc/systemd/system/'+SERVICE); unitfile.parent.mkdir(parents=True,exist_ok=True)
        if unitfile.is_symlink() or any(p.is_symlink() for p in unitfile.parents): raise ValueError('unsafe service path')
        atomic_text(unitfile,unit,0o644)
        drop=dependency_path(root); drop.parent.mkdir(parents=True,exist_ok=True)
        if drop.is_symlink() or any(p.is_symlink() for p in drop.parents): raise ValueError('unsafe login dependency path')
        # All bundle/controller data and a valid boot journal are durable BEFORE
        # the atomic dependency appears. Requires pulls in the service at boot;
        # a separate WantedBy enable step is unnecessary and less crash-safe.
        atomic_json(state/'journal.json',value)
        stage_durability_barrier(root)
        try:
            atomic_text(drop,'[Unit]\nRequires='+SERVICE+'\nAfter='+SERVICE+'\n',0o644)
            run(['systemctl','daemon-reload'],check=True,timeout=30)
            publish_status(root,'checking',status='staged_awaiting_restart')
        except Exception:
            # Persist a valid staged journal until the dependency is removed;
            # a power loss anywhere here still leaves a usable boot transaction.
            try: disarm(root,run)
            except Exception:
                if dependency_path(root).exists(): raise RuntimeError('staged update armed; daemon reload requires review') from None
            atomic_json(state/'journal.json',{**value,'status':'stage_retry','failure_code':'arming_failed'})
            raise ValueError('profile_missing: boot update could not be armed; pupil software unchanged') from None
        return {'status':value['status'],'release':RELEASE,'computer':expected,'package_sha256':PACKAGE_SHA}

def verify_staged(state):
    pins=read_json(state/'pins.json')
    if pins.get('package_sha256')!=PACKAGE_SHA: raise ValueError('staged package binding mismatch')
    manifest=pins['manifest']; bundle=state/'bundle'
    if not ADMIN_UPDATE_FILES.issubset(manifest.get('files',{})): raise ValueError('staged admin interface incomplete')
    actual={p.relative_to(bundle).as_posix() for p in bundle.rglob('*') if not p.is_dir()}
    if actual!=set(manifest['files'])|{'manifest.json'}: raise ValueError('staged inventory changed')
    for name,sha in manifest['files'].items():
        safe_member(name)
        if digest(regular(bundle/name))!=sha: raise ValueError('staged payload changed')
    return bundle,manifest

def make_variables(root,state,kiosk,device,previous,manifest):
    bundle=state/'bundle'; source=bundle/'linux-elevhub'; assets=bundle/'assets'
    variables={k:v for k,v in previous.items() if k.startswith(('kiosk_','cadoodle_'))}
    variables.update(ansible_connection='local',ansible_python_interpreter='/usr/bin/python3',elevhub_firstboot=True,
        kiosk_server=kiosk['server'],kiosk_protokol='https',kiosk_usikker_test=False,
        kiosk_enhedsfil={**device,'ui_token':kiosk['ui_token']},kiosk_ca_fil=str(at(root,'/etc/elevhub/ca.pem')),
        kiosk_school_name=kiosk.get('school_name',''),kiosk_theme=kiosk.get('theme','red'),
        kiosk_genstart_nu=False,kiosk_bruger='elev',cadoodle_aktiveret=True,cadoodle_spring_over_hvis_aaben=False,
        kiosk_elevhub_web=str(source/'server/elevhub/web'),kiosk_lokal_design_kilde=str(bundle),
        kiosk_lokal_package_src=str(source/'projekter'),cadoodle_kilde=str(source/'cadoodle'),
        kiosk_lokale_app_kilder={'scratch':str(assets/'scratch'),'datatraener':str(assets/'datatraener'),
            'kredsloeb':str(source/'server/kredsloeb'),'microbit':str(source/'server/microbit-controller')},
        cadoodle_deb_kilde=str(assets/'CaDoodle.deb'),cadoodle_bootstrap_zip=str(assets/'CaDoodle-ApplicationInstall.zip'),
        cadoodle_bootstrap_zip_sha256=manifest['files']['assets/CaDoodle-ApplicationInstall.zip'],
        cadoodle_kloster_jar=str(assets/'CaDoodle-Kloster.jar'),cadoodle_print_url='',cadoodle_print_ca='')
    admin_config=at(root,'/etc/elevhub/admin-login.json')
    try:
        admin_info=json.loads(regular(admin_config).read_text(encoding='utf-8'))
        admin_name=admin_info.get('username') if isinstance(admin_info,dict) else None
        admin_stat=admin_config.stat()
        if (not isinstance(admin_name,str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}',admin_name)
                or admin_name in ('root','elev','elevhub') or admin_stat.st_uid!=0 or admin_stat.st_mode & 0o022):
            raise ValueError
    except (OSError,ValueError,TypeError,json.JSONDecodeError):
        raise ValueError('profile_missing: existing local administrator identity unavailable') from None
    variables['adminkonto_navn']=admin_name
    # All existing print configuration is retained; the role must not replace it.
    for src,dest in [('internet_app','kiosk_internet_app'),('lokale_app_urler','kiosk_lokale_app_urler')]:
        if src in kiosk: variables[dest]=kiosk[src]
    if 'hub' in kiosk:
        from urllib.parse import urlsplit
        variables['kiosk_hub_port']=urlsplit(kiosk['hub']).port
    ansible=source/'ansible'
    atomic_json(state/'inventory.json',{'elevcomputere':{'hosts':{kiosk['computer']:{'ansible_connection':'local'}}}})
    atomic_json(state/'variables.json',variables)
    # Prevent the role from advertising the new release before postchecks.
    # This is an ephemeral reviewed-role copy, never a source/payload mutation.
    import yaml
    runtime=state/'runtime-roles'
    if runtime.exists(): shutil.rmtree(runtime) # staged journal: software is not yet changed
    shutil.copytree(ansible/'roles',runtime)
    main=runtime/'kiosk/tasks/main.yml'; tasks=yaml.safe_load(main.read_text())
    matching=[t for t in tasks if t.get('ansible.builtin.copy',{}).get('dest')=='/etc/elevhub/release.json']
    if len(matching)!=1: raise ValueError('reviewed kiosk release task missing/ambiguous')
    starters=[t for t in tasks if t.get('ansible.builtin.systemd_service',{}).get('name')=='elevhub-lokal'
        and t['ansible.builtin.systemd_service'].get('state')=='started']
    if len(starters)!=1 or starters[0].get('name')!='Start lokale elevapps':
        raise ValueError('reviewed local project writer start task missing/ambiguous')
    main.write_text(yaml.safe_dump([t for t in tasks if t not in matching+starters],allow_unicode=True,sort_keys=False))
    handlers_file=runtime/'kiosk/handlers/main.yml'; handlers=yaml.safe_load(handlers_file.read_text())
    restarters=[t for t in handlers if t.get('ansible.builtin.systemd_service',{}).get('name')=='elevhub-lokal'
        and t['ansible.builtin.systemd_service'].get('state')=='restarted']
    if len(restarters)!=1 or restarters[0].get('name')!='Genstart lokal elevapp-service':
        raise ValueError('reviewed local project writer restart handler missing/ambiguous')
    # Keep the notify target valid, but defer the actual restart until after
    # byte-for-byte project verification in boot().
    handler=restarters[0]; handler.pop('ansible.builtin.systemd_service')
    handler['ansible.builtin.debug']={'msg':'Local project service restart deferred until update verification'}
    handler.pop('when',None)
    handlers_file.write_text(yaml.safe_dump(handlers,allow_unicode=True,sort_keys=False))
    admin_role=runtime/'adminkonto'
    if any(not (admin_role/relative).is_file() for relative in (
            'tasks/update-interface.yml','files/elevhub-local-update.py',
            'files/elevhub-local-update-gui.py','files/elevhub-admin-xsession.sh',
            'files/elevhub-admin-session.sh','files/elevhub-admin-return.py',
            'templates/admin-bash-profile.j2')):
        raise ValueError('reviewed admin update interface missing')
    # Rebase the task paths in the private runtime copy, keeping the verified
    # bundle immutable across retries and before/after installation checks.
    for item in runtime.rglob('*.yml'):
        text=item.read_text(); item.write_text(text.replace('{{ playbook_dir }}/..',str(source)))
    playbook=state/'queued-update.yml'
    playbook.write_text(json.dumps([{'name':'Existing pupil software update','hosts':'elevcomputere',
        'roles':[str(runtime/'kiosk'),str(runtime/'cadoodle'),
                 {'role':str(admin_role),'tasks_from':'update-interface'}]}]))
    return ansible,playbook

def copy_owned(source,target):
    if source.is_symlink():
        target.symlink_to(os.readlink(source)); shutil.copystat(source,target,follow_symlinks=False)
        st=source.lstat(); os.chown(target,st.st_uid,st.st_gid,follow_symlinks=False)
        return
    if source.is_dir(): shutil.copytree(source,target,symlinks=True)
    else: shutil.copy2(source,target)
    for item in [source,*source.rglob('*')] if source.is_dir() else [source]:
        st=item.lstat(); output=target/item.relative_to(source) if item!=source else target
        os.chown(output,st.st_uid,st.st_gid,follow_symlinks=False)

def _fsync_parent(path):
    try:
        descriptor=os.open(Path(path).parent,os.O_RDONLY|os.O_DIRECTORY)
    except OSError:
        return
    try: os.fsync(descriptor)
    finally: os.close(descriptor)

def _restore_entry(source,target):
    """Restore one entry without deleting its previous value first."""
    source=Path(source); target=Path(target)
    source_dir=source.is_dir() and not source.is_symlink()
    target_dir=target.is_dir() and not target.is_symlink()
    if source_dir:
        if target.exists() and not target_dir:
            raise ValueError('rollback type collision requires review')
        target.mkdir(parents=True,exist_ok=True)
        for child in source.iterdir(): _restore_entry(child,target/child.name)
        for child in list(target.iterdir()):
            source_child=source/child.name
            if not (source_child.exists() or source_child.is_symlink()):
                if child.is_dir() and not child.is_symlink(): shutil.rmtree(child)
                else: child.unlink()
        shutil.copystat(source,target); st=source.stat(); os.chown(target,st.st_uid,st.st_gid)
        _fsync_parent(target); return
    if target.exists() and target_dir:
        raise ValueError('rollback type collision requires review')
    temporary=target.with_name('.'+target.name+'.restore-'+uuid.uuid4().hex)
    if source.is_symlink():
        temporary.symlink_to(os.readlink(source))
        st=source.lstat(); os.chown(temporary,st.st_uid,st.st_gid,follow_symlinks=False)
        shutil.copystat(source,temporary,follow_symlinks=False)
    else:
        shutil.copy2(source,temporary)
        st=source.stat(); os.chown(temporary,st.st_uid,st.st_gid)
        with temporary.open('rb') as stream: os.fsync(stream.fileno())
    os.replace(temporary,target); _fsync_parent(target)

def backup(root,state):
    destination=state/'backup'
    if destination.exists():
        if destination.is_symlink(): raise ValueError('unsafe prior snapshot')
        shutil.rmtree(destination) # journal is staged; no managed software has changed
    destination.mkdir(mode=0o700); existing=[]; index={}
    for name in managed_roots(root):
        source=at(root,name)
        if source.is_symlink(): raise ValueError('managed root symlink requires review')
        if not source.exists(): continue
        target=destination/name.lstrip('/'); target.parent.mkdir(parents=True,exist_ok=True)
        copy_owned(source,target); existing.append(name)
        index[name]=tree(target) if target.is_dir() else digest(target)
    atomic_json(state/'backup-roots.json',existing)
    atomic_json(state/'backup-index.json',index)

def restore(root,state):
    existing=read_json(state/'backup-roots.json'); index=read_json(state/'backup-index.json')
    if not isinstance(existing,list) or any(x not in BACKUP_ROOTS and not re.fullmatch(r'/boot/initrd\.img-[A-Za-z0-9.+_-]+',x) for x in existing):
        raise ValueError('invalid rollback roots')
    # Validate the entire rollback snapshot before changing any live file.
    for name in existing:
        saved=state/'backup'/name.lstrip('/')
        if not saved.exists() or saved.is_symlink(): raise ValueError('rollback snapshot incomplete')
        actual=tree(saved) if saved.is_dir() else digest(saved)
        if index.get(name)!=actual: raise ValueError('rollback snapshot corrupt')
    for name in dict.fromkeys(managed_roots(root)+existing):
        target=at(root,name); source=state/'backup'/name.lstrip('/')
        if name in existing and not source.exists(): raise ValueError('rollback snapshot incomplete')
        # Preserve the root directory itself: /boot may be its own filesystem.
        if name in existing:
            target.parent.mkdir(parents=True,exist_ok=True); _restore_entry(source,target)
        elif target.exists():
            if target.is_dir() and not target.is_symlink():
                for child in list(target.iterdir()):
                    if child.is_dir() and not child.is_symlink(): shutil.rmtree(child)
                    else: child.unlink()
                target.rmdir()
            else: target.unlink()

def active_session(root):
    for entry in at(root,'/proc').glob('[0-9]*'):
        try:
            args=(entry/'cmdline').read_bytes().replace(b'\0',b' ')
            if any(word in args for word in [b'Xorg',b'CaDoodle-Application.jar',b'CaDoodle-Kloster.jar',b'elevbjaelke.py']): return True
        except FileNotFoundError: pass
    return False

def verify_installed(root,manifest,appearance_overrides=None):
    checks={}
    prefixes={'assets/scratch/':'/usr/local/share/elevhub/apps/scratch/',
        'assets/datatraener/':'/usr/local/share/elevhub/apps/datatraener/',
        'linux-elevhub/server/kredsloeb/':'/usr/local/share/elevhub/apps/kredsloeb/',
        'linux-elevhub/server/microbit-controller/':'/usr/local/share/elevhub/apps/microbit/',
        'linux-elevhub/projekter/':'/usr/local/lib/elevhub/projekter/'}
    for name,expected in manifest['files'].items():
        for prefix,destination in prefixes.items():
            if name.startswith(prefix): checks[destination+name[len(prefix):]]=expected
    for name in ['elevhub_klient.py','elevhub-regler.py','workshop_launcher.py','pause_overlay.py',
            'pause_safety.py','browser_close_guard.py','startup_screen.py','proxy_policy.py',
            'elevbjaelke.py','update_status.py','power_controls.py']:
        key='linux-elevhub/ansible/roles/kiosk/files/'+name
        if key in manifest['files']: checks['/usr/local/lib/elevhub/'+name]=manifest['files'][key]
    admin_installed={
        'linux-elevhub/ansible/roles/adminkonto/files/elevhub-local-update.py':'/usr/local/sbin/elevhub-local-update',
        'linux-elevhub/ansible/roles/adminkonto/files/elevhub-local-update-gui.py':'/usr/local/bin/elevhub-local-update-gui',
        'linux-elevhub/ansible/roles/adminkonto/files/elevhub-admin-xsession.sh':'/usr/local/bin/elevhub-admin-xsession',
        'linux-elevhub/ansible/roles/adminkonto/files/elevhub-admin-session.sh':'/usr/local/bin/elevhub-admin-session',
        'linux-elevhub/ansible/roles/adminkonto/files/elevhub-admin-return.py':'/usr/local/sbin/elevhub-admin-return',
    }
    for source,destination in admin_installed.items():
        if source in manifest['files']: checks[destination]=manifest['files'][source]
    power_key='linux-elevhub/ansible/roles/kiosk/files/elevhub-power.py'
    if power_key in manifest['files']:
        checks['/usr/local/sbin/elevhub-power']=manifest['files'][power_key]
    checks['/home/elev/bin/CaDoodle-ApplicationInstall/0.46.094/CaDoodle-Application.jar']=manifest['files']['assets/CaDoodle-Kloster.jar']
    appearance_overrides=appearance_overrides or {}
    for path,expected in checks.items():
        actual=digest(regular(at(root,path)))
        if path in appearance_overrides:
            if actual!=appearance_overrides[path]: raise ValueError('appearance preservation mismatch')
            checks[path]=actual
        elif actual!=expected: raise ValueError('installed software mismatch')
    for path,expected in appearance_overrides.items():
        target=at(root,path)
        actual=tree(target) if target.is_dir() else digest(regular(target))
        if actual!=expected: raise ValueError('appearance preservation mismatch')
        checks[path]=actual
    return checks

def recover(root,state,run,status=None):
    current=journal(state)
    if status: status.phase('recovering')
    phase='stop_project_writer'
    try:
        stop_project_writer(run)
        phase='restore'; restore(root,state); preservation=read_json(state/'preservation.json')
        phase='verify_projects'
        project_result=verify_projects(at(root,'/home/elev/Projekter'),preservation['projects'],
            legacy=bool(preservation.get('project_writer_released')) or
            'preservation_schema' not in preservation)
        phase='verify_identity'
        if tree(at(root,'/etc/elevhub'))!=preservation['identity']: raise ValueError('identity rollback mismatch')
        phase='reload_dbus'; dbus_reload=reload_dbus_after_restore(run)
        preservation={**preservation,'project_writer_released':True,
            'preservation_schema':preservation.get('preservation_schema',2)}
        atomic_json(state/'preservation.json',preservation)
        if Path(root)==Path('/'): os.sync()
        phase='restart_services'
        run(['systemctl','daemon-reload'],check=True)
        for service in ['elevhub-regler','elevhub-lokal','elevhub-design']: run(['systemctl','restart',service],check=True,timeout=90)
        atomic_json(state/'journal.json',{**current,'status':'failed_rolled_back','failed_at':time.time(),
            'project_preservation':project_result,'dbus_reload':dbus_reload,
            'detail':'software/config restored; added OS dependencies may remain; inspect root apply.log'})
        disarm_terminal(root,state,run)
        if status: status.phase('rolled_back')
        return 'failed_rolled_back'
    except Exception as error:
        atomic_json(state/'recovery-failure.json',{'phase':phase,'exception_type':type(error).__name__,
            'failed_at':time.time()})
        atomic_json(state/'journal.json',{**current,'status':'recovery_required','failed_at':time.time(),
            'failure_code':phase,
            'detail':'automatic recovery incomplete; pupil login blocked; inspect root snapshot/log'})
        if status: status.phase('recovery_required')
        raise RuntimeError('recovery_required: pupil login blocked') from None

def boot(root,expected,run=subprocess.run):
    status=BootStatus(root,run)
    try: return _boot(root,expected,run,status)
    finally: status.close()

def _boot(root,expected,run,status):
    if os.geteuid()!=0: raise ValueError('root required')
    state=secure_state(root)
    with lock(state):
        current=journal(state)
        if current.get('computer')!=expected or current.get('release')!=RELEASE: raise ValueError('boot journal identity mismatch')
        if current['status'] in ('complete','failed_rolled_back','failed_before_apply'):
            if current['status'] in ('complete','failed_rolled_back'):
                disarm_terminal(root,state,run)
            publish_status(root, 'complete' if current['status']=='complete' else
                'rolled_back' if current['status']=='failed_rolled_back' else 'failed')
            return current['status']
        if active_session(root): raise ValueError('active pupil session; boot-only update refused')
        if current['status'] in ('applying','recovery_required'):
            status.start()
            return recover(root,state,run,status)
        if current['status']=='staged_awaiting_restart':
            approval=approval_state(root,expected)
            if not approval or not approval['restart_authorized']:
                publish_status(root,'checking',status='staged_awaiting_restart')
                return 'staged_awaiting_restart'
        phase='appearance_preflight'
        try:
            appearance_preflight(root)
            status.start()
            phase='boot_state'
            if current['status']!='staged_awaiting_restart': raise ValueError('unexpected boot state')
            phase='controller_prerequisites'
            if not controller_available(run): raise RuntimeError('local controller prerequisites disappeared after staging')
            phase='existing_profile'; kiosk,device,previous=profile(root,expected)
            phase='bundle_verification'; bundle,manifest=verify_staged(state)
            phase='stop_project_writer'; stop_project_writer(run)
            phase='preservation_inventory'
            projects=tree(at(root,'/home/elev/Projekter')); identity=tree(at(root,'/etc/elevhub'))
            snapshot_appearance(root,state)
            phase='snapshot'; status.phase('snapshot'); backup(root,state)
            phase='runtime_playbook'; ansible,playbook=make_variables(root,state,kiosk,device,previous,manifest)
            atomic_json(state/'preservation.json',{'projects':projects,'identity':identity,
                'project_writers_stopped':True,'preservation_schema':2})
            if Path(root)==Path('/'): os.sync()
        except Exception as error:
            # No pupil software has been modified at this point. Do not call a
            # partially-created snapshot a rollback or leave tty1 dependent on a
            # failed service. A durable terminal success-returning state is
            # written first, so a power loss before disarm still allows login.
            atomic_json(state/'preapply-failure.json',{'phase':phase,'exception_type':type(error).__name__,
                'software_touched':False,'failed_at':time.time()})
            atomic_json(state/'journal.json',{**current,'status':'failed_before_apply','failure_code':phase,
                'software_touched':False,'failed_at':time.time()})
            try: run(['systemctl','restart','elevhub-lokal.service'],check=True,timeout=90)
            except Exception as restart_error:
                atomic_json(state/'service-restart-failure.json',{'phase':phase,
                    'exception_type':type(restart_error).__name__,'failed_at':time.time()})
            try: disarm(root,run)
            except Exception: pass # terminal journal returns success at any later boot
            status.phase('failed')
            return 'failed_before_apply'
        atomic_json(state/'journal.json',{**current,'status':'applying','started_at':time.time()})
        publish_status(root,'installing',status='applying',started_at=status.started)
        try:
            status.phase('installing')
            env=os.environ.copy(); env.update(ANSIBLE_CONFIG=str(ansible/'ansible.cfg'),ANSIBLE_ROLES_PATH=str(ansible/'roles'),DEBIAN_FRONTEND='noninteractive')
            with (state/'apply.log').open('w') as output:
                os.chmod(state/'apply.log',0o600)
                run(['ansible-playbook',str(playbook),'-i',str(state/'inventory.json'),'-e','@'+str(state/'variables.json')],
                    cwd=ansible,env=env,stdout=output,stderr=subprocess.STDOUT,check=True,timeout=6600)
            stop_project_writer(run)
            appearance=restore_appearance(root,state)
            status.phase('verifying')
            saved=state/'backup/etc/elevhub'
            for name in identity:
                old=saved/name; target=at(root,'/etc/elevhub')/name
                if old.is_file() and not old.is_symlink() and name!='release.json': shutil.copy2(old,target)
            after=tree(at(root,'/etc/elevhub'))
            if any(after.get(k)!=v for k,v in identity.items() if k!='release.json'): raise ValueError('identity/config preservation failed')
            project_result=verify_projects(at(root,'/home/elev/Projekter'),projects)
            profile(root,expected)
            installed=verify_installed(root,manifest,appearance.get('overrides'))
            atomic_json(state/'preservation.json',{'projects':projects,'identity':identity,
                'project_writers_stopped':True,'preservation_schema':2,'project_writer_released':True})
            if Path(root)==Path('/'): os.sync()
            for service in ['elevhub-regler','elevhub-design','elevhub-lokal']:
                run(['systemctl','restart',service],check=True,timeout=90); run(['systemctl','is-active','--quiet',service],check=True,timeout=30)
            shutil.copy2(bundle/'linux-elevhub/server/elevhub/release.json',at(root,'/etc/elevhub/release.json'))
            atomic_json(state/'journal.json',{**current,'status':'complete','completed_at':time.time(),
                'preservation_verified':True,'project_preservation':project_result,'installed_files':installed,
                'appearance_overrides':appearance.get('overrides',{})})
            disarm_terminal(root,state,run)
            status.phase('complete')
            return 'complete'
        except Exception: return recover(root,state,run,status)

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--boot',choices=NAMES,required=True)
    args=parser.parse_args(); print(boot(Path('/'),args.boot))

if __name__=='__main__': main()
