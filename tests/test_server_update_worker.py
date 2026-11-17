"""Native server update contract with synthetic files; never starts an EXE."""
import copy
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

SOURCE = Path(__file__).resolve().parents[1]
WORKER = SOURCE / 'system/windows-elevhub/server/server_update_worker.py'


class ServerUpdateWorkerTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(WORKER.is_file(), 'A native signed server updater is required')
        spec = importlib.util.spec_from_file_location('native_server_update_worker', WORKER)
        self.module = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.module)
        self.temp = tempfile.TemporaryDirectory(prefix='mkv-server-update-test-'); self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.program, self.data, self.store = (root/n for n in ('program','data','store'))
        for p in (self.program/'hub', self.data/'installer-updates', self.store/'inbox'):
            p.mkdir(parents=True)
        (self.data/'printvaerksted').mkdir()
        with closing(sqlite3.connect(self.data/'printvaerksted/queue.sqlite3')) as db:
            db.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)')
            db.execute("INSERT INTO settings VALUES('paused','true')")
            db.commit()
        self.write(self.program/'runtime-release.json', {'schema':1,'role':'server','release_id':'2.1.0-20261117.3'})
        self.write(self.program/'hub/konfiguration.windows.json', {'simple_updates':{
            'repository':'mdkmsuv/makervaerkstedet','public_key':'public-fixture',
            'minisign_exe':str(self.program/'tools/minisign/minisign.exe'), 'minisign_sha256':'b'*64}})
        self.write(self.data/'konfiguration.json',{'elev_adresse':'192.0.2.2','backend_ports':{'print':8765},
            'apps':[{'id':'synthetic-workshop','port':8770}], 'simple_updates':{
                **json.loads((self.program/'hub/konfiguration.windows.json').read_bytes())['simple_updates'],
                'device_bindings':{'synthetic-device':'preserved'},'catalog':[{'id':'synthetic-app'}]}})
        self.write(self.data/'school.json',{'name':'Synthetic school','admin_public_key':'public-sentinel'})
        self.write(self.data/'security/ca.key',{'synthetic_key':'not-a-real-key'})
        self.payload=b'MZsynthetic-server-exe'
        self.manifest={'schema':1,'repository':'mdkmsuv/makervaerkstedet','role':'server',
            'platform':'windows-amd64','component':'server','version':'2.1.0-20261117.4','sequence':2026111704,
            'artifacts':[{'name':'Makervaerkstedet-server-Setup.exe','package':None,'architecture':'amd64',
                'version':'2.1.0-20261117.4','size':len(self.payload),'sha256':hashlib.sha256(self.payload).hexdigest()}]}
        self.responses={};self.requests=[];self.verifications=[]
        self.configure_release()
        self.worker=self.module.Worker(self.store,self.program,self.data,opener=self.open,
            verifier=self.verify,health_check=lambda expected:True)

    def write(self,p,value):
        p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(json.dumps(value),encoding='utf-8')

    def configure_release(self):
        prefix='https://github.com/mdkmsuv/makervaerkstedet/releases/download/v'+self.manifest['version']+'/'
        self.responses={'https://api.github.com/repos/mdkmsuv/makervaerkstedet/releases/latest':
            json.dumps({'tag_name':'v'+self.manifest['version'],'draft':False,'prerelease':False}).encode(),
            prefix+'makervaerkstedet-server.json':json.dumps(self.manifest).encode(),
            prefix+'makervaerkstedet-server.json.minisig':b'synthetic-signature',
            prefix+self.manifest['artifacts'][0]['name']:self.payload}

    def open(self,url,repository):
        self.requests.append(url)
        return io.BytesIO(self.responses[url])

    def verify(self,raw,signature,**kwargs):
        self.verifications.append(kwargs)
        if signature!=b'synthetic-signature':raise ValueError('invalid_signature')
        from app.simple_release import _wire
        value=_wire(raw,kwargs['repository'],kwargs['role'],kwargs['platform'],kwargs['component'],kwargs['now'])
        value['_manifest_sha256']=hashlib.sha256(raw).hexdigest()
        return value

    def request(self,action,expected=None):
        request={'schema':1,'action':action,'command_id':str(uuid.uuid4()),'manifest_sha256':expected}
        self.write(self.store/'inbox/request.json',request)
        return request

    def check(self):
        self.request('check');self.worker.check()
        return self.worker.status()

    def prepare(self):
        status=self.check();request=self.request('install',status['candidate']['manifest_sha256'])
        return self.worker.prepare(),request

    def complete(self,plan):
        self.write(self.program/'runtime-release.json',{'schema':1,'role':'server','release_id':plan['version']})
        journal=self.data/'installer-updates'/plan['command_id']
        self.write(journal/'state.json',{'phase':'complete_paused','hold_released':True,'operation_id':journal.name})
        return journal

    def test_check_verifies_bound_server_manifest_without_downloading_exe(self):
        status=self.check()
        self.assertEqual(status['state'],'available')
        self.assertEqual(status['installed_version'],'2.1.0-20261117.3')
        self.assertEqual(status['candidate']['version'],'2.1.0-20261117.4')
        self.assertFalse(any(url.endswith('.exe') for url in self.requests))
        self.assertEqual(self.verifications[0]['role'],'server')
        self.assertEqual(self.verifications[0]['public_key'],'public-fixture')

    def test_prepare_requires_exact_selected_hash_and_downloads_verified_owned_file(self):
        plan,request=self.prepare()
        self.assertEqual(set(plan),{'exe','sha256','command_id','version'})
        self.assertEqual(Path(plan['exe']).read_bytes(),self.payload)
        self.assertTrue(Path(plan['exe']).is_relative_to(self.store/'downloads'))
        self.assertEqual(plan['command_id'],request['command_id'])
        self.assertEqual(self.worker.status()['state'],'installing')
        self.assertEqual(json.loads((self.store/'receipts'/f"{request['command_id']}.json").read_bytes()),request)

    def test_changed_selection_is_rejected_before_download(self):
        self.check();self.request('install','f'*64)
        with self.assertRaises(ValueError):self.worker.prepare()
        self.assertFalse(any(url.endswith('.exe') for url in self.requests))

    def test_tampered_download_never_produces_install_plan(self):
        status=self.check();self.request('install',status['candidate']['manifest_sha256'])
        url=next(u for u in self.responses if u.endswith('.exe'));self.responses[url]=b'X'*len(self.payload)
        with self.assertRaises(ValueError):self.worker.prepare()
        self.assertNotEqual(self.worker.status()['state'],'installing')

    def test_old_sequence_and_same_sequence_different_bytes_are_rejected(self):
        self.check()
        for sequence in (2026111703,2026111704):
            self.manifest['sequence']=sequence;self.manifest['version']='2.1.0-20261117.5'
            self.manifest['artifacts'][0]['version']=self.manifest['version'];self.configure_release();self.request('check')
            with self.subTest(sequence=sequence),self.assertRaises(ValueError):self.worker.check()

    def test_wrong_signature_tag_role_or_artifact_version_is_rejected(self):
        original=copy.deepcopy(self.manifest)
        for case in ('signature','tag','role','artifact_version'):
            self.manifest=copy.deepcopy(original)
            if case=='role':self.manifest['role']='laser_station'
            if case=='artifact_version':self.manifest['artifacts'][0]['version']='2.1.0-20261117.2'
            self.configure_release()
            if case=='signature':self.responses[next(u for u in self.responses if u.endswith('.minisig'))]=b'wrong'
            if case=='tag':self.responses[next(u for u in self.responses if u.endswith('/latest'))]=b'{"tag_name":"v2.1.0-20261117.9","draft":false,"prerelease":false}'
            self.request('check')
            with self.subTest(case=case),self.assertRaises((ValueError,KeyError)):self.worker.check()

    def test_same_installed_version_is_not_offered_for_reinstallation(self):
        self.write(self.program/'runtime-release.json',{'schema':1,'role':'server','release_id':self.manifest['version']})
        status=self.check();self.assertEqual(status['state'],'current')
        self.request('install',status['candidate']['manifest_sha256'])
        with self.assertRaises(ValueError):self.worker.prepare()

    def test_unknown_request_fields_and_paths_are_rejected(self):
        for change in ({'command_id':'../../other'}, {'command_id':str(uuid.UUID(int=0))}, {'action':'shell'}, {'exe':'calc.exe'}):
            request=self.request('check');request.update(change);self.write(self.store/'inbox/request.json',request)
            with self.subTest(change=change),self.assertRaises(ValueError):self.worker.check()
        self.assertFalse(self.requests)

    def test_finish_requires_new_runtime_native_completion_and_health(self):
        plan,_=self.prepare()
        self.worker.finish(0)
        self.assertEqual(self.worker.status()['state'],'interrupted')
        self.assertTrue(Path(plan['exe']).exists())

    def test_success_removes_only_download_and_preserves_receipt_and_backup(self):
        plan,request=self.prepare()
        journal=self.complete(plan)
        self.write(journal/'saved-data.json',{'synthetic':'kept'})
        self.worker.finish(0)
        self.assertEqual(self.worker.status()['state'],'succeeded')
        self.assertTrue(self.worker.status()['queues_paused'])
        self.assertFalse(Path(plan['exe']).exists())
        self.assertTrue((journal/'saved-data.json').is_file())
        self.assertTrue((self.store/'receipts'/f"{request['command_id']}.json").is_file())

    def test_exit_failure_never_reports_success_or_deletes_download(self):
        plan,_=self.prepare();self.worker.finish(20)
        self.assertEqual(self.worker.status()['state'],'failed')
        self.assertTrue(Path(plan['exe']).exists())

    def test_old_native_success_cannot_be_reused_as_new_completion(self):
        journal=self.data/'installer-updates'/str(uuid.uuid4())
        self.write(journal/'state.json',{'phase':'complete_paused','hold_released':True,'operation_id':journal.name})
        plan,_=self.prepare()
        self.write(self.program/'runtime-release.json',{'schema':1,'role':'server','release_id':plan['version']})
        self.worker.finish(0)
        self.assertEqual(self.worker.status()['state'],'interrupted')

    def test_version_changed_during_download_never_produces_plan(self):
        original=self.worker.download
        def concurrent_install(url,limit,destination=None):
            result=original(url,limit,destination)
            if destination:
                self.write(self.program/'runtime-release.json',{'schema':1,'role':'server','release_id':self.manifest['version']})
            return result
        self.worker.download=concurrent_install
        with self.assertRaisesRegex(ValueError,'server_version_changed'):self.prepare()
        self.assertFalse((self.store/'operation.json').exists())

    def test_wrong_or_extra_journal_cannot_change_operation_binding(self):
        plan,_=self.prepare();self.complete(plan)
        other=self.data/'installer-updates'/str(uuid.uuid4())
        self.write(other/'state.json',{'phase':'complete_paused','hold_released':True,'operation_id':other.name})
        self.assertEqual(self.worker.finish(0)['state'],'succeeded')

    def test_configuration_or_key_change_prevents_success(self):
        plan,_=self.prepare();self.complete(plan)
        config=json.loads((self.data/'konfiguration.json').read_bytes());config['backend_ports']['print']=8999
        self.write(self.data/'konfiguration.json',config)
        self.assertEqual(self.worker.finish(0)['state'],'interrupted')
        self.assertEqual(self.worker.status()['code'],'protected_data_changed')
        self.assertTrue(Path(plan['exe']).exists())

    def test_cache_cleanup_failure_is_not_installation_failure(self):
        plan,_=self.prepare();self.complete(plan)
        with patch.object(self.worker,'cleanup_download',side_effect=PermissionError('locked')):
            result=self.worker.finish(0)
        self.assertEqual(result['state'],'succeeded');self.assertEqual(result['code'],'download_retained')
        self.assertTrue(Path(plan['exe']).exists())

    def test_check_reconciles_completed_crash_without_new_download(self):
        plan,_=self.prepare();journal=self.complete(plan);previous=list(self.requests)
        value=json.loads((journal/'state.json').read_bytes())
        # The actual native rollback inventory exceeded 1 MiB, unlike the
        # small updater request/status files that have a 64 KiB limit.
        value['program_backup']={'files':{f'web/synthetic-asset-{i}.js':'a'*64 for i in range(15000)}}
        self.write(journal/'state.json',value)
        self.assertGreater((journal/'state.json').stat().st_size,1024**2)
        self.request('check');result=self.worker.check()
        self.assertEqual(result['state'],'succeeded');self.assertEqual(result['command_id'],plan['command_id'])
        self.assertEqual(self.requests,previous)

    def test_oversized_native_journal_remains_unresolved_and_preserves_download(self):
        plan,_=self.prepare();journal=self.complete(plan)
        value=json.loads((journal/'state.json').read_bytes())
        value['program_backup']={'synthetic_oversize':'x'*(16*1024**2)}
        self.write(journal/'state.json',value)
        result=self.worker.finish(0)
        self.assertEqual(result['state'],'interrupted')
        self.assertEqual(result['code'],'metadata_too_large')
        self.assertTrue(Path(plan['exe']).exists())

    def test_lost_finish_reply_cannot_clobber_confirmed_success(self):
        plan,_=self.prepare();self.complete(plan)
        self.assertEqual(self.worker.finish(0)['state'],'succeeded')
        self.assertEqual(self.worker.fail()['state'],'succeeded')

    def test_unknown_install_outcome_survives_fail_and_check(self):
        plan,_=self.prepare();previous=list(self.requests)
        self.assertEqual(self.worker.fail()['state'],'interrupted')
        self.request('check');self.assertEqual(self.worker.check()['state'],'interrupted')
        self.assertEqual(self.requests,previous);self.assertTrue(Path(plan['exe']).exists())
        self.assertEqual(json.loads((self.store/'operation.json').read_bytes())['command_id'],plan['command_id'])

    def test_proven_rollback_is_terminal_failure_and_preserves_download(self):
        plan,_=self.prepare()
        journal=self.data/'installer-updates'/plan['command_id']
        self.write(journal/'state.json',{'phase':'program_restored_paused','hold_released':True,'operation_id':journal.name})
        self.assertEqual(self.worker.finish(None)['state'],'failed')
        self.assertTrue(Path(plan['exe']).exists())

    def test_pre_operation_id_installer_is_rejected(self):
        self.manifest['version']='2.1.0-20261117.3';self.manifest['artifacts'][0]['version']=self.manifest['version'];self.configure_release()
        self.request('check')
        with self.assertRaisesRegex(ValueError,'installer_too_old'):self.worker.check()


if __name__=='__main__':unittest.main()
