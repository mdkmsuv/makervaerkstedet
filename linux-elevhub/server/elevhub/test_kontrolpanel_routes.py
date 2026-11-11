"""Real HTTP regression for the new panel entry and unchanged teacher boundary."""
import http.client,json,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
import elevhub
from tilstand import Tilstand,Laererkode

class PanelHTTP(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name);self.web=root/'web';self.web.mkdir();self.hub=root/'hub';self.hub.mkdir()
  self.files={'kontrolpanel.html':b'<html><head></head><body>NEW PANEL</body></html>','laerer.html':b'OLD PANEL','kontrolpanel.css':b'body{color:green}','kontrolpanel.js':b'/* panel client */','update-admin.js':b'window.UpdateAdmin={};'}
  for name,raw in self.files.items():(self.web/name).write_bytes(raw)
  self.patch=patch.object(elevhub,'WEB',self.web);self.patch.start()
  self.apps=[{'id':'3dprint','navn':'3D-print','port':8770,'type':'vaerksted'},{'id':'internet','navn':'Internet','type':'browser'}]
  self.state=Tilstand(self.hub,self.apps);self.code=Laererkode(self.hub);self.code.vaelg('synthetic-password')
  self.server=elevhub.Server(('127.0.0.1',0),elevhub.Laerer);self.server.navn='laerer';self.server.tilstand=self.state;self.server.kode=self.code;self.server.apps=self.apps
  self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.origin='http://127.0.0.1:'+str(self.server.server_port)
 def tearDown(self):
  self.server.shutdown();self.server.server_close();self.thread.join();self.patch.stop();self.tmp.cleanup()
 def request(self,path,body=None,cookie=None,headers=None):
  headers=dict(headers or {});raw=None
  if body is not None:raw=json.dumps(body).encode();headers['Content-Type']='application/json'
  if cookie:headers['Cookie']=cookie
  c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3);c.request('GET' if body is None else 'POST',path,raw,headers);r=c.getresponse();result=(r.status,dict(r.getheaders()),r.read());c.close();return result
 def login(self):
  status,headers,_=self.request('/api/laerer/login',{'kode':'synthetic-password'});self.assertEqual(status,200);self.assertIn('HttpOnly',headers['Set-Cookie']);self.assertIn('SameSite=Strict',headers['Set-Cookie']);return headers['Set-Cookie'].split(';')[0]
 def test_root_and_named_entry_serve_new_panel(self):
  for path in ('/','/kontrolpanel'):
   status,headers,raw=self.request(path);self.assertEqual(status,200);self.assertEqual(raw,self.files['kontrolpanel.html']);self.assertNotIn(b'OLD PANEL',raw);self.assertEqual(headers['X-Content-Type-Options'],'nosniff')
 def test_panel_assets_and_existing_update_module_are_reachable(self):
  for path,kind in (('/kontrolpanel.css','text/css'),('/kontrolpanel.js','javascript'),('/update-admin.js','javascript')):
   status,headers,raw=self.request(path);self.assertEqual(status,200);self.assertIn(kind,headers['Content-Type']);self.assertEqual(raw,self.files[path[1:]])
  self.assertEqual(self.request('/installation-owner.json')[0],404)
 def test_teacher_session_and_overview_keep_cookie_boundary(self):
  self.assertFalse(json.loads(self.request('/api/laerer/session')[2])['logget_ind'])
  self.assertEqual(self.request('/api/laerer/oversigt',headers={'Authorization':'Bearer synthetic-ui-or-root-token'})[0],401)
  cookie=self.login();self.assertTrue(json.loads(self.request('/api/laerer/session',cookie=cookie)[2])['logget_ind'])
  status,_,body=self.request('/api/laerer/oversigt',cookie=cookie);self.assertEqual(status,200);overview=json.loads(body);self.assertEqual({a['id'] for a in overview['apps']},{'3dprint','internet'});self.assertIn('computere',overview)
 def test_real_app_actions_require_same_origin_and_allow_explicit_internet(self):
  cookie=self.login();body={'id':'internet','aaben':True}
  for origin in (None,'http://outside.invalid'):
   headers={} if origin is None else {'Origin':origin};self.assertEqual(self.request('/api/laerer/app',body,cookie,headers)[0],403);self.assertFalse(self.state.aaben('internet'))
  self.assertEqual(self.request('/api/laerer/app',body,cookie,{'Origin':self.origin})[0],200);self.assertTrue(self.state.aaben('internet'))
 def test_teacher_setup_remains_local_only(self):
  with patch.object(elevhub.Laerer,'lokal',return_value=False):
   self.assertEqual(self.request('/api/laerer/opsaet',{'kode':'new-password','kode_igen':'new-password'})[0],403)

if __name__=='__main__':unittest.main()
