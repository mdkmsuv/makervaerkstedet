import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile
from datetime import datetime
from unittest.mock import Mock, patch
from types import SimpleNamespace
from scripts.github_update_service import server as update_server
spec=importlib.util.spec_from_file_location('school_update',Path(__file__).with_name('update.py'))
u=importlib.util.module_from_spec(spec);spec.loader.exec_module(u)

class UpdateTests(unittest.TestCase):
 def test_runtime_test_data_is_excluded_but_runtime_licenses_are_kept(self):
  self.assertTrue(u.runtime_member_excluded('runtime/Lib/site-packages/numpy/_core/tests/data/astype_copy.pkl'))
  self.assertTrue(u.runtime_member_excluded('runtime/Lib/site-packages/numpy/tests/test_numeric.py'))
  self.assertFalse(u.runtime_member_excluded('runtime/Lib/site-packages/numpy/testing/__init__.py'))
  self.assertFalse(u.runtime_member_excluded('runtime/Lib/site-packages/numpy/core/_multiarray_umath.pyd'))
  self.assertFalse(u.runtime_member_excluded('runtime/LICENSE.txt'))
  self.assertFalse(u.runtime_member_excluded('runtime/Lib/site-packages/numpy-2.5.3.dist-info/licenses/numpy/LICENSE.txt'))

 def test_old_values_and_rows_must_survive(self):
  u.assert_preserved({'jobs':[{'id':'a','status':'done'}]},{'jobs':[{'id':'a','status':'done','new':0}]})
  with self.assertRaises(ValueError): u.assert_preserved({'jobs':[{'id':'a'}]},{'jobs':[]})
  with self.assertRaises(ValueError): u.assert_preserved({'jobs':[{'id':'a','status':'done'}]},{'jobs':[{'id':'a','status':'queued'}]})
 def test_plain_rejects_links(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'file').write_text('x')
   try:(p/'link').symlink_to(p/'file')
   except OSError:self.skipTest('symlinks unavailable')
   with self.assertRaises(ValueError):u.plain(p/'link')
 def test_relative_paths_refuse_escape(self):
  for value in ('../app.py','/app.py','C:/app.py','app\\x.py',''):
   with self.assertRaises(ValueError):u.safe_relative(value)
 def fixture(self,t):
  root=Path(t);inst={r:root/r for r in ('hub','print')};data={r:root/('data-'+r) for r in inst};candidate=root/'candidate';runtime={}
  for name,value in [('python.exe',b'exe'),('python314.dll',b'dll'),('python314.zip',b'zip')]:
   p=candidate/'hub/runtime'/name;p.parent.mkdir(parents=True,exist_ok=True)
   if name.endswith('.zip'):
    with zipfile.ZipFile(p,'w') as archive:
     archive.writestr(zipfile.ZipInfo('fixture.py',date_time=(1980,1,1,0,0,0)),b'# fixture stdlib\n')
   else:p.write_bytes(value)
   runtime['runtime/'+name]=u.hashfile(p)
  for role in inst:
   (inst[role]/'runtime').mkdir(parents=True);(inst[role]/'app').mkdir();(inst[role]/'app/core.py').write_text('old')
   (inst[role]/'installation.json').write_text('{}');u.write(inst[role]/'integrity.json',{'algorithm':'sha256','files':{'app/core.py':u.hashfile(inst[role]/'app/core.py')}})
   for name in ('python.exe','python314.dll','python314.zip'):(inst[role]/'runtime'/name).write_bytes((candidate/'hub/runtime'/name).read_bytes())
   (candidate/role/'app').mkdir(parents=True,exist_ok=True);(candidate/role/'app/core.py').write_text('new')
   for rel in u.APPEARANCE_HOOKS[role]:
    hook=candidate/role/rel;hook.parent.mkdir(parents=True,exist_ok=True);hook.write_text('LOCAL_APPEARANCE_VERSION = 1\n')
   data[role].mkdir(parents=True)
   appearance=data[role]/'local-appearance';appearance.mkdir()
   (appearance/'style.css').write_text('local-school-skin',encoding='utf-8')
   u.write(appearance/'manifest.json',{'schemaVersion':1,'school_name':'Testskolen','theme':'green','style':{'static':{'/style.css':{'path':'style.css','sha256':u.hashfile(appearance/'style.css')}}}})
  u.write(data['hub']/'konfiguration.json',{'existing':'preserve'})
  u.write(data['hub']/'school.json',{'schemaVersion':1,'school_name':'Testskolen','theme':'green','role':'vm','server':'192.0.2.10','student_subnet':'192.0.2.0/24','admin_public_key':'ssh-ed25519 AAAAtest'})
  (data['hub']/'hub').mkdir();(data['hub']/'designvaerksted').mkdir();(data['print']/'supervisor').mkdir()
  for db in (data['print']/'queue.sqlite3',data['hub']/'designvaerksted/afleveringer.sqlite3'):
   c=sqlite3.connect(db);c.execute('create table jobs(id text,status text)');c.execute("insert into jobs values('one','queued')");c.execute('create table settings(key text primary key,value text)');c.commit();c.close()
  files={role:u.tree(candidate/role) for role in inst};manifest={'format':u.FORMAT,'version':'test','trusted_runtime':runtime,'files':files,'appearance_hook':{role:{'schemaVersion':1,'files':{rel:files[role][rel] for rel in u.APPEARANCE_HOOKS[role]}} for role in inst}};u.write(candidate/'candidate.json',manifest)
  return inst,data,candidate,root/'journal'
 def test_snapshot_captures_committed_wal(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);r=p/'source';r.mkdir();db=r/'q.sqlite3'
   c=sqlite3.connect(db);c.execute('pragma journal_mode=wal');c.execute('create table jobs(id text)');c.execute("insert into jobs values('durable')");c.commit()
   u.snapshot_data(p/'backup',{'hub':r})
   self.assertEqual(u.database(p/'backup/hub/q.sqlite3')['jobs'],[{'id':'durable'}]);c.close()
 def test_long_path_preflight_fails_closed_when_policy_is_disabled(self):
  with patch.object(u,'windows_long_paths_enabled',return_value=False,create=True):
   with self.assertRaisesRegex(ValueError,'LongPathsEnabled'):
    u.require_windows_long_paths()
 def test_direct_apply_checks_long_paths_before_verifying_or_creating_journal(self):
  with tempfile.TemporaryDirectory() as d:
   journal=Path(d)/'journal'
   with patch.object(u,'windows_long_paths_enabled',return_value=False),patch.object(u,'verify',side_effect=AssertionError('candidate verification ran before policy guard')) as verify:
    with self.assertRaisesRegex(ValueError,'LongPathsEnabled'):
     u.apply(Path(d)/'candidate',journal,'a'*64)
    verify.assert_not_called()
   self.assertFalse(journal.exists())
 def test_native_long_path_preflight_precedes_snapshots_and_task_stops(self):
  script=Path(__file__).with_name('Invoke-SchoolUpdate.ps1').read_text(encoding='utf-8')
  preflight=script.index("Invoke-CheckedPython -Arguments @('long-path-preflight')")
  for marker in ("$taskBackup=$Journal+'.tasks'",'Disable-ScheduledTask','Stop-ScheduledTask',
                 "Invoke-CheckedPython -Arguments @('apply'"):
   with self.subTest(marker=marker): self.assertLess(preflight,script.index(marker))
 def test_long_path_setting_requires_machine_dword_one(self):
  registry=SimpleNamespace(HKEY_LOCAL_MACHINE=object(),REG_DWORD=4,OpenKey=Mock(return_value='key'),
                           QueryValueEx=Mock(return_value=(1,4)),CloseKey=Mock())
  self.assertTrue(u.windows_long_paths_enabled(registry,platform='nt'))
  registry.QueryValueEx.return_value=(0,4)
  self.assertFalse(u.windows_long_paths_enabled(registry,platform='nt'))
  registry.QueryValueEx.return_value=(1,1)
  self.assertFalse(u.windows_long_paths_enabled(registry,platform='nt'))
  registry.OpenKey.side_effect=FileNotFoundError
  self.assertFalse(u.windows_long_paths_enabled(registry,platform='nt'))
 def test_controller_blocks_before_apply_when_long_path_policy_is_disabled(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);installed=root/'installed-hub';installed.mkdir()
   u.write(installed/'school-update.json',{'version':'2025.01.01'})
   config={'release_id':'release-1','version':'2026.02.01','candidate':str(root/'candidate'),
           'candidate_sha256':'a'*64,'package_sha256':'b'*64}
   profile={'layout':'school-flat-v1','worker_target':'server','baseline_version':'2025.01.01'}
   admin=SimpleNamespace(worker_allowed=lambda *_:True,worker_job=lambda *_:{'id':'job-1'},report=Mock())
   updater=Mock()
   updater.INSTALLS={'hub':installed}
   updater.verify.return_value={'version':config['version']}
   updater.require_windows_long_paths.side_effect=u.LongPathPolicyError(u.LONG_PATH_ERROR)
   invoke=Mock()
   with patch.object(update_server,'load',return_value=updater):
    update_server.tick(root,config,profile,admin,root,invoke)
   invoke.assert_not_called()
   admin.report.assert_called_once_with('release-1','server','blocked',code='windows_long_paths_disabled')
   self.assertFalse((root/'operation-job-1/controller.json').exists())
 def test_controller_retry_after_policy_fix_reuses_empty_operation_folder(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);installed=root/'installed-hub';installed.mkdir()
   u.write(installed/'school-update.json',{'version':'2025.01.01'})
   config={'release_id':'release-1','version':'2026.02.01','candidate':str(root/'candidate'),
           'candidate_sha256':'a'*64,'package_sha256':'b'*64}
   profile={'layout':'school-flat-v1','worker_target':'server','baseline_version':'2025.01.01'}
   admin=SimpleNamespace(worker_allowed=lambda *_:True,worker_job=lambda *_:{'id':'job-1'},report=Mock())
   updater=Mock()
   updater.INSTALLS={'hub':installed}
   updater.verify.return_value={'version':config['version']}
   updater.assert_idle=Mock(return_value={})
   updater.require_windows_long_paths.side_effect=[u.LongPathPolicyError(u.LONG_PATH_ERROR),None]
   invoked=[]
   def invoke(argv,**_kwargs):
    invoked.append(argv)
    journal=Path(argv[argv.index('-Journal')+1])
    update_server.write(journal/'journal.json',{'state':'complete','version':config['version'],
                                               'candidate_sha256':config['candidate_sha256']})
    return SimpleNamespace(returncode=0)
   with patch.object(update_server,'load',return_value=updater):
    update_server.tick(root,config,profile,admin,root,Mock())
    operation=root/'operation-job-1'
    self.assertTrue(operation.is_dir())
    self.assertFalse((operation/'controller.json').exists())
    update_server.tick(root,config,profile,admin,root,invoke)
   self.assertEqual(len(invoked),1)
   self.assertEqual([call.args[2] for call in admin.report.call_args_list],['blocked','applying','complete'])
   self.assertTrue((operation/'controller.json').is_file())
 def test_school_overlay_survives_candidate_asset_update_and_has_timestamp(self):
  with tempfile.TemporaryDirectory() as t:
   inst,data,candidate,journal=self.fixture(t)
   for role in inst:
    installed_asset=inst[role]/'web/school-theme.css';installed_asset.parent.mkdir(parents=True,exist_ok=True);installed_asset.write_text('local-school-skin')
    candidate_asset=candidate/role/'web/school-theme.css';candidate_asset.parent.mkdir(parents=True,exist_ok=True);candidate_asset.write_text('release-skin')
   files={role:u.tree(candidate/role) for role in inst};manifest={'format':u.FORMAT,'version':'test','trusted_runtime':{k:u.hashfile(candidate/'hub'/k) for k in ('runtime/python.exe','runtime/python314.dll','runtime/python314.zip')},'files':files,'appearance_hook':{role:{'schemaVersion':1,'files':{rel:files[role][rel] for rel in u.APPEARANCE_HOOKS[role]}} for role in inst}}
   u.write(candidate/'candidate.json',manifest);expected=u.hashfile(candidate/'candidate.json')
   with patch.object(u,'INSTALLS',inst),patch.object(u,'DATA',data),patch.object(u,'assert_idle',return_value={}),patch.dict('sys.modules',{'psutil':SimpleNamespace(process_iter=lambda fields:[])}),patch.object(u.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='{}',stderr='')):
    u.apply(candidate,journal,expected)
   appearance=data['hub']/'local-appearance'
   self.assertEqual((appearance/'style.css').read_text(), 'local-school-skin')
   self.assertEqual((inst['hub']/'web/school-theme.css').read_text(),'release-skin')
   receipt=u.read(journal/'customization-before/receipt.json')
   datetime.fromisoformat(receipt['captured_utc'])
   self.assertEqual(u.read(inst['hub']/'school-update.json')['customization']['profiles'],receipt['profiles'])
   with patch.object(u,'INSTALLS',inst),patch.object(u,'DATA',data):
    self.assertTrue(u.installed(candidate,expected)['school_customization_verified'])
    (appearance/'style.css').write_text('tampered')
    with self.assertRaises(ValueError):u.installed(candidate,expected)
 def test_legacy_missing_profile_is_recorded_without_inventing_one(self):
  with tempfile.TemporaryDirectory() as t:
   inst,data,candidate,journal=self.fixture(t);(data['hub']/'school.json').unlink()
   receipt=u.capture_customization(journal/'customization-before',data,inst)
   self.assertIsNone(receipt['profiles']['hub'])
   self.assertFalse((data['hub']/'school.json').exists())
 def test_candidate_without_appearance_hook_is_refused(self):
  with tempfile.TemporaryDirectory() as t:
   inst,data,candidate,journal=self.fixture(t);manifest=u.read(candidate/'candidate.json');manifest.pop('appearance_hook');u.write(candidate/'candidate.json',manifest)
   with self.assertRaises(ValueError):u.verify(candidate)
 def test_local_appearance_mutation_is_detected(self):
  with tempfile.TemporaryDirectory() as t:
   inst,data,candidate,journal=self.fixture(t);u.capture_customization(journal/'customization-before',data,inst);u.restore_customization(journal/'customization-before',data,inst)
   target=data['hub']/'local-appearance/manifest.json';target.write_text(target.read_text().replace('green','red'))
   with self.assertRaises(ValueError):u.verify_customization(journal/'customization-before',data,inst)
 def test_success_preserves_identity_pauses_and_records_selected_version(self):
  with tempfile.TemporaryDirectory() as t:
   inst,data,candidate,journal=self.fixture(t)
   with patch.object(u,'INSTALLS',inst),patch.object(u,'DATA',data),patch.object(u,'assert_idle',return_value={}),patch.dict('sys.modules',{'psutil':SimpleNamespace(process_iter=lambda fields:[])}),patch.object(u.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='{"rows_preserved":true}',stderr='')):
    u.apply(candidate,journal,u.hashfile(candidate/'candidate.json'))
   self.assertEqual((inst['hub']/'installation.json').read_text(),'{}')
   self.assertEqual(u.read(inst['hub']/'school-update.json')['version'],'test')
   self.assertEqual(u.read(data['hub']/'konfiguration.json')['existing'],'preserve')
   self.assertEqual(u.database(data['print']/'queue.sqlite3')['jobs'],[{'id':'one','status':'queued'}])
   self.assertIn({'key':'paused','value':'true'},u.database(data['print']/'queue.sqlite3')['settings'])
 def test_failed_migration_rolls_code_and_data_back(self):
  with tempfile.TemporaryDirectory() as t:
   inst,data,candidate,journal=self.fixture(t)
   for role in inst:
    legacy=inst[role]/('web/'+('laerer.css' if role=='hub' else 'style.css'));legacy.parent.mkdir(parents=True,exist_ok=True);legacy.write_text('legacy skin')
   results=[SimpleNamespace(returncode=0,stdout='{}',stderr=''),SimpleNamespace(returncode=0,stdout='{}',stderr=''),SimpleNamespace(returncode=1,stdout='',stderr='migration failure')]
   with patch.object(u,'INSTALLS',inst),patch.object(u,'DATA',data),patch.object(u,'assert_idle',return_value={}),patch.dict('sys.modules',{'psutil':SimpleNamespace(process_iter=lambda fields:[])}),patch.object(u.subprocess,'run',side_effect=results):
    with self.assertRaises(ValueError):u.apply(candidate,journal,u.hashfile(candidate/'candidate.json'))
   self.assertEqual((inst['hub']/'app/core.py').read_text(),'old')
   self.assertFalse((inst['hub']/'school-update.json').exists())
   self.assertEqual(u.read(data['hub']/'konfiguration.json'),{'existing':'preserve'})
   self.assertEqual((data['hub']/'local-appearance/style.css').read_text(), 'local-school-skin')
 def test_second_capture_keeps_saved_skin_instead_of_new_release_styles(self):
  with tempfile.TemporaryDirectory() as t:
   inst,data,candidate,journal=self.fixture(t)
   first=u.capture_customization(journal/'first',data,inst)
   (inst['hub']/'web').mkdir();(inst['hub']/'web/style.css').write_text('different-release')
   second=u.capture_customization(journal/'second',data,inst)
   self.assertEqual(first['assets'],second['assets'])
 def test_external_print_profile_preserved_and_absence_checked(self):
  with tempfile.TemporaryDirectory() as t:
   inst,data,candidate,journal=self.fixture(t)
   profile=data['print'].parent/'school.json';profile.write_text('{"theme":"green"}')
   u.capture_customization(journal/'before',data,inst)
   profile.write_text('{"theme":"red"}')
   with self.assertRaises(ValueError):u.verify_customization(journal/'before',data,inst)
   u.restore_customization(journal/'before',data,inst)
   self.assertEqual(profile.read_text(),'{"theme":"green"}')
 def test_candidate_tamper_refused(self):
  with tempfile.TemporaryDirectory() as t:
   inst,data,candidate,journal=self.fixture(t);(candidate/'hub/app/core.py').write_text('tampered')
   with self.assertRaises(ValueError):u.verify(candidate)
 def test_integrity_keeps_legacy_flat_print_format(self):
  with tempfile.TemporaryDirectory() as t:
   inst,data,candidate,journal=self.fixture(t)
   original={'app/core.py':u.hashfile(inst['print']/'app/core.py')}
   u.write(inst['print']/'integrity.json',original)
   with patch.object(u,'INSTALLS',inst),patch.object(u,'DATA',data),patch.object(u,'assert_idle',return_value={}),patch.dict('sys.modules',{'psutil':SimpleNamespace(process_iter=lambda fields:[])}),patch.object(u.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='{}',stderr='')):
    u.apply(candidate,journal,u.hashfile(candidate/'candidate.json'))
   actual=u.read(inst['print']/'integrity.json')
   self.assertNotIn('files',actual)
   self.assertEqual(actual['app/core.py'],u.hashfile(candidate/'print/app/core.py'))
 def test_integrity_refuses_unknown_schema_before_copy(self):
  with self.assertRaises(ValueError):u.merge_integrity({'format':'unknown'}, {})
  with self.assertRaises(ValueError):u.merge_integrity({'algorithm':'sha1','files':{}}, {})
if __name__=='__main__':unittest.main(verbosity=2)

