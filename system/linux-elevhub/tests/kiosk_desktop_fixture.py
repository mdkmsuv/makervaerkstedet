from pathlib import Path as _LayoutPath
import sys as _layout_sys
_LAYOUT_ROOT = next(p for p in _LayoutPath(__file__).resolve().parents if (p / 'REPOSITORY-LAYOUT.json').is_file())
_layout_sys.path[:0] = [str(_LAYOUT_ROOT), str(_LAYOUT_ROOT / 'system')]
from repository import source_path, repo_root
import sys,json,threading,tempfile,importlib.util,shutil,queue
from pathlib import Path
from http.server import ThreadingHTTPServer
ROOT=Path(sys.argv[1])
REAL_LAUNCHER='--real-launcher' in sys.argv
TOKEN='synthetic-local-launcher-token-12345678' if REAL_LAUNCHER else 'fixture-token'
sys.path[:0]=[str(ROOT),str(source_path('linux-elevhub/projekter', ROOT))]
from lokal import make_server
from projektarkiv import Projektarkiv
from app.laser_local import create_local_servers,create_print_server
spec=importlib.util.spec_from_file_location('kiosk',source_path('linux-elevhub/tests/kiosk_browser_fixture.py', ROOT))
kiosk=importlib.util.module_from_spec(spec);spec.loader.exec_module(kiosk)
with tempfile.TemporaryDirectory(prefix='mkv-ux-review-') as temp:
 local=make_server(8791 if REAL_LAUNCHER else 0,Projektarkiv(Path(temp)/'archive'))
 local.home_url=f'http://127.0.0.1:{local.server_port}/_elevhub/projekter/home'
 laser,folie=create_local_servers('http://127.0.0.1:1',laser_port=0,folie_port=0,projects_port=local.server_port)
 model=create_print_server('http://127.0.0.1:1',port=0,projects_port=local.server_port)
 urls={'laser':f'http://127.0.0.1:{laser.port}/laser.html','folie':f'http://127.0.0.1:{folie.port}/folie.html','3dprint':f'http://127.0.0.1:{model.port}/model3d.html','projekter':f'http://127.0.0.1:{local.server_port}/'}
 local.workshop_urls.update(urls)
 original_manifests=kiosk.manifests
 def manifests(mode):
  result=original_manifests(mode)
  for m in result:
   if m['id'] in urls:m['start']=urls[m['id']]
  return result
 kiosk.manifests=manifests
 original_apps=kiosk.apps
 def apps(mode):
  result=original_apps(mode)
  for name in ['folie','3dprint']:
   result.append({'id':name,'navn':name,'aaben':mode!='closed','klar':True,'type':'vaerksted','port':8796 if name=='folie' else 8798})
  return result
 kiosk.apps=apps
 def render(colours='none'):
  env=kiosk.Environment(loader=kiosk.FileSystemLoader(str(kiosk.TEMPLATE_DIR)),autoescape=False)
  env.filters['to_json']=lambda value:json.dumps(value,ensure_ascii=False)
  return env.get_template('start.html.j2').render(kiosk_server='127.0.0.1',kiosk_protokol='http',kiosk_hub_port=kiosk.PORT,inventory_hostname='browser-fixture',kiosk_enhed={'ui_token':TOKEN},kiosk_theme='red',kiosk_brand=kiosk.COLOURS[colours][0],kiosk_brand_dark=kiosk.COLOURS[colours][1],kiosk_school_name='Testskolen',kiosk_lokale_app_urler={**kiosk.APP_URLS,**urls},kiosk_internet_start='https://internet.example.test/').encode('utf-8')
 kiosk.render=render
 kiosk.Handler.mode='open'
 hub=ThreadingHTTPServer(('127.0.0.1',0),kiosk.Handler);kiosk.PORT=hub.server_port
 # Serve the exact rendered home through the project service to exercise the
 # real HTTP return path from the shelf (Chromium blocks HTTP -> file://).
 home=Path(temp)/'home';home.mkdir();(home/'assets').mkdir()
 for name in ['creative-hub.css','creative-hub.js','workshops.js','Manrope.ttf','favicon.svg',
              'workshop-folie-v2.webp','workshop-laser-v2.webp','workshop-3d-v2.webp',
              'workshop-scratch-v2.png','workshop-microbit-v2.png','workshop-internet-v2.png']:
  shutil.copyfile(kiosk.ASSETS/name,home/'assets'/name)
 (home/'start.html').write_bytes(render())
 local.kiosk_start_file=home/'start.html'
 local.kiosk_config=Path(temp)/'kiosk.json'
 local.kiosk_config.write_text(json.dumps({'theme':'red','school_name':'Testskolen','hub':f'http://127.0.0.1:{hub.server_port}'}))
 servers=[local,laser,folie,model,hub]
 stop=threading.Event()
 if REAL_LAUNCHER:
  sys.path.insert(0,str(source_path('linux-elevhub/ansible/roles/kiosk/files', ROOT)))
  import workshop_launcher
  # Real HTTP/authentication/CORS handler; only installed catalogue and the
  # native unsaved-work callback are synthetic. Never launch native jobs.
  workshop_launcher.installed_apps=lambda _root:manifests(kiosk.Handler.catalog)
  launcher=workshop_launcher.make_server('browser-fixture',TOKEN,lambda:False)
  servers.append(launcher)
  def finish_requests():
   while not stop.is_set():
    try:request=launcher.requests.get(timeout=.1)
    except queue.Empty:continue
    with launcher.state_lock:
     launcher.results[request['id']]={'id':request['id'],'state':'completed'}
     launcher.pending_id=launcher.pending_action=None
  threading.Thread(target=finish_requests,daemon=True).start()
 for server in servers:threading.Thread(target=server.serve_forever,daemon=True).start()
 print(json.dumps({**urls,'hub':f'http://127.0.0.1:{hub.server_port}'}),flush=True)
 try:sys.stdin.readline()
 finally:
  stop.set()
  for server in servers:server.shutdown();server.server_close()
