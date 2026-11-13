import sys,json,threading,tempfile,importlib.util
from pathlib import Path
from http.server import ThreadingHTTPServer
ROOT=Path(sys.argv[1])
sys.path[:0]=[str(ROOT),str(ROOT/'linux-elevhub/projekter')]
from lokal import make_server
from projektarkiv import Projektarkiv
from app.laser_local import create_local_servers,create_print_server
spec=importlib.util.spec_from_file_location('kiosk',ROOT/'linux-elevhub/tests/kiosk_browser_fixture.py')
kiosk=importlib.util.module_from_spec(spec);spec.loader.exec_module(kiosk)
with tempfile.TemporaryDirectory(prefix='mkv-ux-review-') as temp:
 local=make_server(0,Projektarkiv(Path(temp)/'archive'))
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
  return env.get_template('start.html.j2').render(kiosk_server='127.0.0.1',kiosk_protokol='http',kiosk_hub_port=kiosk.PORT,inventory_hostname='browser-fixture',kiosk_enhed={'ui_token':'fixture-token'},kiosk_theme='red',kiosk_brand=kiosk.COLOURS[colours][0],kiosk_brand_dark=kiosk.COLOURS[colours][1],kiosk_school_name='Testskolen',kiosk_lokale_app_urler={**kiosk.APP_URLS,**urls},kiosk_internet_start='https://internet.example.test/').encode('utf-8')
 kiosk.render=render
 kiosk.Handler.mode='open'
 hub=ThreadingHTTPServer(('127.0.0.1',0),kiosk.Handler);kiosk.PORT=hub.server_port
 servers=[local,laser,folie,model,hub]
 for server in servers:threading.Thread(target=server.serve_forever,daemon=True).start()
 print(json.dumps({**urls,'hub':f'http://127.0.0.1:{hub.server_port}'}),flush=True)
 try:sys.stdin.readline()
 finally:
  for server in servers:server.shutdown();server.server_close()
