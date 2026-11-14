"""Synthetic generic app, project service and stateless app for browser QA."""
from pathlib import Path as _LayoutPath
import sys as _layout_sys
_LAYOUT_ROOT = next(p for p in _LayoutPath(__file__).resolve().parents if (p / 'REPOSITORY-LAYOUT.json').is_file())
_layout_sys.path[:0] = [str(_LAYOUT_ROOT), str(_LAYOUT_ROOT / 'system')]
from repository import source_path, repo_root
import json
from pathlib import Path
import sys
import tempfile
import threading
import signal
import uuid

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(source_path('linux-elevhub/projekter', ROOT))]
from tests.test_app_catalog import descriptor
from app_host import discover_apps
from projektarkiv import Projektarkiv
from lokal import make_server,Handler
from workshop_sessions import WorkshopSessions

with tempfile.TemporaryDirectory(prefix='mkv-browser-app-') as temp:
    root=Path(temp)
    for name,project in [('noter',True),('quiz',False)]:
        folder=root/'apps'/name;www=folder/'www';www.mkdir(parents=True)
        app=descriptor(name,8799 if project else 8800,project=project)
        (folder/'app.json').write_text(json.dumps(app))
        (www/'index.html').write_text('<!doctype html><html lang="da"><head><meta charset="utf-8"><title>Noter</title><script src="/editor.js" defer></script></head><body><main><h1>'+('Noter' if project else 'Quiz uden gemning')+'</h1><label for="notes">Din tekst</label><textarea id="notes"></textarea></main></body></html>')
        script=''
        if project:
            script="""let projectName='Note';const notes=document.querySelector('#notes');window.ElevhubProjectAdapter={type:'noter',extension:'.noter.json',name:()=>projectName,setName:n=>{projectName=n},hasContent:()=>Boolean(notes.value),export:async()=>new Blob([JSON.stringify({notes:notes.value})],{type:'application/json'}),import:async blob=>{notes.value=JSON.parse(await blob.text()).notes},reset:()=>{notes.value='';projectName='Note'}};"""
        (www/'editor.js').write_text(script)
    apps=discover_apps(root/'apps');store=Projektarkiv(root/'state',apps=apps)
    sessions=WorkshopSessions(kinds={'noter'})
    servers=[];urls={};stop=threading.Event();token=uuid.uuid4().hex
    shelf=make_server(0,store,apps=apps,sessions=sessions);servers.append(shelf)
    class FixtureHandler(Handler):
        def do_POST(self):
            if self.path=='/_qa-stop/'+token:
                self.svar(200,{'stopping':True});stop.set()
            else:self.dispatch()
    shelf.RequestHandlerClass=FixtureHandler
    for app in apps:
        server=make_server(0,store,app=app['id'],assets=app['assets'],apps=apps,sessions=sessions,descriptor=app,workshops={})
        app['port']=server.server_port;app['start']=f'http://127.0.0.1:{server.server_port}/'
        urls[app['id']]=app['start'];servers.append(server)
    for server in servers:threading.Thread(target=server.serve_forever,daemon=True).start()
    print(json.dumps({'shelf':f'http://127.0.0.1:{shelf.server_port}/_elevhub/projekter/',
                      'cleanup':f'http://127.0.0.1:{shelf.server_port}/_qa-stop/{token}',**urls}),flush=True)
    try:stop.wait()
    except KeyboardInterrupt:pass
    finally:
        for server in servers:server.shutdown();server.server_close()
