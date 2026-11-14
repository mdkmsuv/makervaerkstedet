"""Installed, declarative apps. No app Python/import hooks or LAN listeners."""
from __future__ import annotations
import json
import logging
import mimetypes
from pathlib import Path
from urllib.parse import urlsplit
from app_manifests import load_manifests

PROJECT_BRIDGE='<link rel="stylesheet" href="/_elevhub/projekter/bridge.css"><script src="/_elevhub/projekter/bridge.js" defer></script>'
SHARED=frozenset(('/school-theme.js','/school-theme.css','/favicon.svg','/project-import.js',
                  '/assets/metrophobic-latin.ttf','/assets/Metrophobic-OFL.txt'))
LEGACY_PROJECTS={'scratch':('.sb3','scratch'),'microbit':('.microbit.json','microbit'),
                 'laser':('.laser.json','laser'),'folie':('.svg','folie'),'3dprint':('.mkv3d','3dprint')}


def uses_gateway(app):
    try:
        from app.app_catalog import GATEWAY_CAPABILITIES
    except ModuleNotFoundError:
        from app_catalog import GATEWAY_CAPABILITIES
    caps=app.get('capabilities',{})
    return app.get('aflevering') is not None or any(c in GATEWAY_CAPABILITIES
            for c in caps.get('required',[])+caps.get('optional',[]))


def discover_apps(root=Path('/usr/share/makervaerkstedet/apps'),legacy_root=Path('/usr/local/share/elevhub/apps'),*,strict=True):
    result=[]
    def invalid(name,error):logging.warning('App %s springes over: %s',name,type(error).__name__)
    try:manifests=load_manifests(root,on_error=None if strict else invalid)
    except ValueError:
        if strict:raise
        logging.error('Appmappen kunne ikke valideres; basis fortsætter uden apps.')
        return []
    for app in manifests:
        port=urlsplit(app['start']).port
        if port in (8790,8791):
            if strict:raise ValueError('duplicate_app_port')
            invalid(app['id'],ValueError('duplicate_app_port'));continue
        app=dict(app)
        if app['schema']==1:
            project=LEGACY_PROJECTS.get(app['id'])
            app['project']={'kind':project[1],'extension':project[0],'max_bytes':50*1024**2} if project else None
            app['category']='kode' if app['id'] in ('scratch','microbit') else 'byg'
            app['capabilities']={'required':['projects-v1'] if project else [],'optional':[]}
        assets=Path(root)/app['id']/'www'
        if app['id']=='scratch' and not assets.is_dir():assets=Path(legacy_root)/'scratch'
        if not assets.is_dir():continue  # Incomplete app cannot take down the shelf.
        app.update(port=port,assets=str(assets))
        result.append(app)
    rejected=set()
    for index,app in enumerate(result):
        for other in result[index+1:]:
            conflict='duplicate_app_port' if app['port']==other['port'] else None
            if app.get('project') and other.get('project') and (app['project']['kind']==other['project']['kind'] or app['project']['extension']==other['project']['extension']):
                conflict='duplicate_app_project_format'
            if conflict:
                if strict:raise ValueError(conflict)
                rejected.update((app['id'],other['id']))
                invalid(app['id'],ValueError(conflict));invalid(other['id'],ValueError(conflict))
    return [app for app in result if app['id'] not in rejected]


def runtime_endpoints(apps):
    endpoints=[(8791,'/_elevhub/health','elevhub-lokal','projekter')]
    for app in apps:
        endpoints.append((app['port'],'/api/runtime/health' if uses_gateway(app) else '/_elevhub/health',
                          'elevhub-design' if uses_gateway(app) else 'elevhub-lokal',app['id']))
    return endpoints


def asset(app,path,shared_root=None):
    """Bounded package tree; never follow a link or expose app.json/parent dirs."""
    if path=='/_elevhub/app.js':
        public={key:app.get(key) for key in ('id','navn','version','sdk','project','capabilities')}
        return ('window.ElevhubApp='+json.dumps(public,ensure_ascii=True).replace('<','\\u003c')+';').encode(),'text/javascript; charset=utf-8'
    if path=='/project-import-config.js':
        return b'window.ElevhubProjectOrigins=["http://127.0.0.1:8791","http://localhost:8791"];','text/javascript; charset=utf-8'
    aliases={'laser':{'/':'/laservaerksted/index.html','/laser.html':'/laservaerksted/index.html'},
             'folie':{'/':'/laser.html','/folie.html':'/laser.html'},'3dprint':{'/':'/model3d.html'}}
    path=aliases.get(app['id'],{}).get(path,path)
    if path=='/':path=urlsplit(app['start']).path
    if path=='/':path='/index.html'
    if not path.startswith('/') or '%' in path or '\\' in path or any(p in ('','..','.') for p in path[1:].split('/')):return None
    root=Path(app['assets']);target=root/path[1:]
    if not target.is_file() and path in SHARED and shared_root is not None:
        root=Path(shared_root);target=root/path[1:]
    if any(p.is_symlink() for p in (target,*target.parents)) or not target.is_file() or target.stat().st_size>64*1024**2:return None
    data=target.read_bytes()
    mime={'.js':'text/javascript','.wasm':'application/wasm'}.get(target.suffix) or mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
    if mime=='text/html':
        injection='<script src="/_elevhub/app.js"></script>'
        capabilities=app['capabilities']
        if 'theme-v1' in capabilities['required']+capabilities['optional'] and b'school-theme.js' not in data:
            injection+='<link rel="stylesheet" href="/school-theme.css"><script src="/school-theme.js" defer></script>'
        if app.get('project') is not None:injection+=PROJECT_BRIDGE
        data=data.replace(b'</head>',(injection+'</head>').encode(),1)
    return data,mime
