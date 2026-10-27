import hashlib,importlib.util,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock

spec=importlib.util.spec_from_file_location('device_updates',Path(__file__).with_name('device_updates.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class DeviceUpdateTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.sha='a'*64;self.version='2026.10.27';self.rid='github-linux_student-'+self.version
  self.release={'id':self.rid,'role':'linux_student','version':self.version,'package_sha256':self.sha,'reviewed':True}
  self.target={'id':'pupil-one','worker_target':'pupil-one','role':'linux_student','release_ids':[self.rid]}
  self.admin=Mock();self.admin.root=Path(self.temp.name);self.admin.configuration.return_value=({self.rid:self.release},{'pupil-one':self.target});self.admin.release_ready.return_value=True
  self.admin.snapshot.return_value={'targets':[{'id':'pupil-one','job':None}]}
  self.feed=Mock();self.feed.snapshot.return_value={'state':'available','enabled':True,'latest':{'repository':'example/product','release_id':123,'version':self.version,'notes':'Release notes','packages':[{'role':'linux_student','sha256':self.sha,'asset':'linux-update.zip'}]},'downloads':[]}
  self.service=m.DeviceUpdates(self.admin,self.feed)
 def body(self,action='update',rid='1'*32):return {'request_id':rid,'action':action,'version':self.version,'package_sha256':self.sha}
 def test_reading_status_never_downloads_or_enqueues(self):
  out=self.service.snapshot('pupil-one');self.assertEqual(out['available']['version'],self.version)
  self.assertNotIn('worker_root',json.dumps(out));self.admin.execute.assert_not_called();self.feed.execute.assert_not_called()
 def test_explicit_request_enqueues_only_authenticated_computer_once(self):
  self.service.execute('pupil-one',self.body());self.service.process_pending();self.service.process_pending()
  call=self.admin.execute.call_args.args[1];self.assertEqual(call['params'],{'release_id':self.rid,'target_ids':['pupil-one']});self.assertEqual(self.admin.execute.call_count,1)
 def test_request_survives_restart_and_download_before_import(self):
  self.admin.configuration.return_value=({}, {'pupil-one':self.target})
  self.service.execute('pupil-one',self.body());self.service.process_pending()
  self.assertEqual(self.feed.execute.call_args.args[1]['action'],'download');self.admin.execute.assert_not_called()
  self.admin.configuration.return_value=({self.rid:self.release},{'pupil-one':self.target})
  m.DeviceUpdates(self.admin,self.feed).process_pending();self.admin.execute.assert_called_once()
 def test_wrong_hash_other_target_and_ui_fields_rejected(self):
  for body in ({**self.body(),'package_sha256':'b'*64},{**self.body(),'target_id':'other'},{**self.body(),'version':'2025.01.01'}):
   with self.assertRaises(ValueError):self.service.execute('pupil-one',body)
  with self.assertRaises(ValueError):self.service.execute('other',self.body())
  self.admin.execute.assert_not_called()
 def test_replay_and_new_release_do_not_change_original_order(self):
  self.service.execute('pupil-one',self.body())
  with self.assertRaises(ValueError):self.service.execute('pupil-one',{**self.body(),'package_sha256':'b'*64})
  self.service.execute('pupil-one',self.body());self.service.process_pending();self.admin.execute.assert_called_once()
 def test_failed_enqueue_requires_explicit_retry(self):
  self.admin.execute.side_effect=ValueError('private details')
  self.service.execute('pupil-one',self.body());self.service.process_pending();self.service.process_pending()
  self.assertEqual(self.admin.execute.call_count,1);out=self.service.snapshot('pupil-one')
  self.assertEqual(out['request']['status'],'blocked');self.assertNotIn('private details',json.dumps(out))
 def test_catalog_hash_cannot_replace_approved_package(self):
  self.service.execute('pupil-one',self.body());self.release['package_sha256']='b'*64
  self.service.process_pending();self.admin.execute.assert_not_called()
 def test_check_only_requests_discovery(self):
  self.service.execute('pupil-one',{'request_id':'2'*32,'action':'check'})
  self.assertEqual(self.feed.execute.call_args.args[1]['action'],'check');self.admin.execute.assert_not_called()
 def test_failed_download_stops_until_explicit_retry(self):
  self.admin.configuration.return_value=({}, {'pupil-one':self.target})
  self.service.execute('pupil-one',self.body());self.service.process_pending()
  self.feed.snapshot.return_value['downloads']=[{'release_id':123,'asset':'linux-update.zip','status':'failed'}]
  self.service.process_pending()
  self.assertEqual(self.service.snapshot('pupil-one')['request']['status'],'blocked')
  self.assertEqual(self.feed.execute.call_count,1)
  self.service.execute('pupil-one',self.body('retry','4'*32));self.service.process_pending()
  self.assertEqual(self.feed.execute.call_count,2)
 def test_completed_release_cannot_be_retried(self):
  self.service.execute('pupil-one',self.body());self.service.process_pending()
  self.admin.snapshot.return_value={'targets':[{'id':'pupil-one','job':{'status':'completed','release_id':self.rid}}]}
  with self.assertRaises(ValueError):self.service.execute('pupil-one',self.body('retry','5'*32))
 def test_newer_feed_does_not_prevent_retrying_existing_package(self):
  self.service.execute('pupil-one',self.body());self.service.process_pending()
  self.feed.snapshot.return_value['latest']['version']='2026.10.28'
  self.feed.snapshot.return_value['latest']['packages'][0]['sha256']='b'*64
  self.service.execute('pupil-one',self.body('retry','6'*32));self.service.process_pending()
  self.assertEqual(self.service.snapshot('pupil-one')['request']['version'],self.version)
 def test_second_release_allowed_only_after_completed_job(self):
  self.service.execute('pupil-one',self.body());self.service.process_pending()
  self.feed.snapshot.return_value['latest']['version']='2026.10.28'
  self.feed.snapshot.return_value['latest']['packages'][0]['sha256']='b'*64
  body={**self.body(rid='3'*32),'version':'2026.10.28','package_sha256':'b'*64}
  with self.assertRaises(ValueError):self.service.execute('pupil-one',body)
  self.admin.snapshot.return_value={'targets':[{'id':'pupil-one','job':{'status':'completed','release_id':self.rid}}]}
  self.service.execute('pupil-one',body)
  self.assertEqual(self.service.snapshot('pupil-one')['request']['version'],'2026.10.28')
  self.assertIsNone(self.service.snapshot('pupil-one')['job'])
  self.feed.snapshot.return_value['latest']['version']='2026.10.29'
  with self.assertRaises(ValueError):
   self.service.execute('pupil-one',{**body,'request_id':'7'*32,'version':'2026.10.29'})

class EndpointTests(unittest.TestCase):
 def test_root_auth_required_and_no_browser_origin(self):
  import elevhub
  handler=Mock();handler.headers={};handler.enhed.return_value=None
  elevhub.Hub.device_update(handler,25)
  handler.enhed.assert_called_once_with('root');handler.laes_json.assert_not_called();handler.server.device_updates.execute.assert_not_called()
  handler=Mock();handler.headers={'Origin':'http://untrusted'}
  elevhub.Hub.device_update(handler,25)
  self.assertEqual(handler.svar.call_args.args[0],403);handler.enhed.assert_not_called()
 def test_target_is_from_authenticated_identity(self):
  import elevhub
  handler=Mock();handler.headers={};handler.enhed.return_value='pupil-one';handler.laes_json.return_value={'action':'check','request_id':'1'*32}
  elevhub.Hub.device_update(handler,25)
  handler.server.device_updates.execute.assert_called_once_with('pupil-one',handler.laes_json.return_value)

class LocalHelperIntegrationTests(unittest.TestCase):
 """Use actual helper, feed and durable queue; only transport is replaced."""
 def setUp(self):
  from update_admin import UpdateAdministration
  from release_feed import ReleaseFeed
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.root=Path(self.temp.name);self.sha='a'*64;self.version='2026.10.27'
  self.rid='github-linux_student-'+self.version
  self.admin=UpdateAdministration(self.root/'server');self.feed=ReleaseFeed(self.root/'server')
  worker=self.root/'worker';worker.mkdir();self.write(worker/'config.json',{'release':self.version})
  identity=self.root/'identity.json';self.write(identity,{'enheder':{'pupil-one':{'token_sha256':'b'*64}}})
  key=self.root/'key';key.write_text('isolated fixture only')
  known=self.root/'known';known.write_text('pupil-one ssh-ed25519 AAAATEST\n')
  release={'id':self.rid,'role':'linux_student','version':self.version,'reviewed':True,
           'worker_root':str(worker),'manifest_sha256':hashlib.sha256((worker/'config.json').read_bytes()).hexdigest(),'package_sha256':self.sha}
  target={'id':'pupil-one','name':'Test computer','role':'linux_student','worker_target':'pupil-one',
          'release_ids':[self.rid],'identity_file':str(identity),'identity_sha256':'b'*64,'ssh_key':str(key),'known_hosts':str(known)}
  self.write(self.admin.root/'catalog.json',{'schema':1,'releases':[release]})
  self.write(self.admin.root/'targets.json',{'schema':1,'targets':[target]})
  self.feed.execute('fixture',{'request_id':'f'*32,'action':'configure','params':{'repository':'example/project'}})
  self.service=m.DeviceUpdates(self.admin,self.feed)
  path=Path(__file__).parents[2]/'ansible/roles/adminkonto/files/elevhub-local-update.py'
  spec=importlib.util.spec_from_file_location('local_update_integration',path)
  self.helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.helper)
  self.paths=self.helper.Paths.for_test(self.root/'pupil')
  self.write(self.paths.kiosk,{'computer':'pupil-one','hub':'https://server.example.test:8773','ca_fil':str(self.paths.ca),'theme':'green'})
  fixture_token=hashlib.sha256(b'isolated-local-admin-test').hexdigest()
  self.write(self.paths.device,{'computer':'pupil-one','token':fixture_token})
  self.write(self.paths.admin,{'username':'administrator'})
  self.write(self.paths.release,{'release_id':'2026.10.26'})
  self.client=Mock();self.client.hent_json.side_effect=self.transport
 @staticmethod
 def write(path,value):
  path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value),encoding='utf-8')
 def transport(self,hub,path,*,token,computer,ca_fil,data=None,timeout=15):
  self.assertEqual(path,'/api/enhed/opdatering');self.assertEqual(computer,'pupil-one')
  return self.service.snapshot(computer) if data is None else self.service.execute(computer,data)
 def test_real_helper_orders_and_retries_real_queue(self):
  state=self.helper.perform('state',self.paths,client=self.client)
  self.assertEqual(state['installed_version'],'2026.10.26')
  self.helper.perform('update',self.paths,client=self.client)
  approval=json.loads(self.paths.approval.read_text())
  self.assertFalse(approval['restart_authorized']);self.assertEqual(approval['package_sha256'],self.sha)
  self.service.process_pending()
  self.assertEqual(self.service.snapshot('pupil-one')['job']['status'],'pending')
  self.assertTrue(self.admin.worker_allowed(self.rid,'pupil-one'))
  self.admin.report(self.rid,'pupil-one','blocked',code='local_admin_required')
  self.assertEqual(self.service.snapshot('pupil-one')['job']['code'],'local_admin_required')
  self.helper.perform('retry',self.paths,client=self.client);self.service.process_pending()
  self.assertEqual(self.service.snapshot('pupil-one')['job']['status'],'pending')
 def test_real_helper_check_requests_feed_without_enqueue(self):
  with self.feed.connect() as db:db.execute('UPDATE state SET requested=0 WHERE id=1')
  self.helper.perform('check',self.paths,client=self.client)
  with self.feed.connect() as db:self.assertEqual(db.execute('SELECT requested FROM state WHERE id=1').fetchone()[0],1)
  self.assertIsNone(self.service.snapshot('pupil-one')['job'])

if __name__=='__main__':unittest.main()
