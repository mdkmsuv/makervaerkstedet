"""Guarded laser updater checks. No SSH, installation or laser activity."""
from contextlib import contextmanager
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from scripts.queued_laser_update import build, queue_update as queue, remote_runner as remote

class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.install=self.root/'station';self.install.mkdir();self.candidate=self.root/'candidate';self.candidate.mkdir()
        self.config=self.install/'laser-installation.json';self.config.write_text(json.dumps({'role':'Station','server':'https://school:8780','station_id':'laser-1','machine':'fbb2','plates':'preserved'}))
        self.old=self.install/'app/sample.py';self.old.parent.mkdir();self.old.write_bytes(b'old = True\n')
        (self.candidate/'app').mkdir();(self.candidate/'app/sample.py').write_bytes(b'new = True\n')
        self.manifest={'release':'test','files':{'app/sample.py':{'sha256':remote.digest(self.candidate/'app/sample.py'),'size':len(b'new = True\n')}}}
        self.operation='a'*32;self.archive_hash='b'*64
    def apply(self):return remote.apply(self.install,self.candidate,self.manifest,self.operation,self.archive_hash)
    def test_preservation_and_idempotency(self):
        protected={'school-ca.pem':b'CA','connection.json':b'connection','runtime/python.exe':b'runtime','plates/student.svg':b'project','keys/private':b'key'}
        for name,content in protected.items():path=self.install/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
        before=self.config.read_bytes()
        with patch.object(remote,'candidate_probe') as probe:
            result=self.apply();self.assertEqual(result['status'],'complete');self.assertTrue(result['configuration_preserved']);self.assertEqual(self.apply()['status'],'complete');self.assertEqual(probe.call_count,1)
        self.assertEqual(self.old.read_bytes(),b'new = True\n');self.assertEqual(self.config.read_bytes(),before)
        for name,content in protected.items():self.assertEqual((self.install/name).read_bytes(),content)
        self.assertEqual(remote.read_json(self.install/'integrity.json')['app/sample.py'],remote.digest(self.old))
    def test_runtime_failure_rolls_back_then_manual_retry_can_succeed(self):
        with patch.object(remote,'candidate_probe',side_effect=ValueError('runtime')):
            with self.assertRaises(ValueError):self.apply()
        self.assertEqual(self.old.read_bytes(),b'old = True\n');self.assertFalse((self.install/'queued-release.json').exists())
        with patch.object(remote,'candidate_probe'):self.assertEqual(self.apply()['status'],'complete')
        self.assertEqual(len(list((self.install/'queued-update-backups').glob('*-attempt-*'))),1)
    def powerloss(self):
        original=remote.atomic
        def atomic(path,data):
            original(path,data)
            if path==self.old:raise KeyboardInterrupt('simulated power loss')
        with patch.object(remote,'atomic',side_effect=atomic),patch.object(remote,'candidate_probe'):
            with self.assertRaises(KeyboardInterrupt):self.apply()
    def test_powerloss_applying_recovers_before_and_requires_next_retry(self):
        self.powerloss();self.assertEqual(self.old.read_bytes(),b'new = True\n')
        result=self.apply();self.assertEqual(result['status'],'failed_rolled_back');self.assertEqual(self.old.read_bytes(),b'old = True\n')
        with patch.object(remote,'candidate_probe'):self.assertEqual(self.apply()['status'],'complete')
    def test_recovery_never_erases_unknown_edit(self):
        self.powerloss();self.old.write_bytes(b'teacher unrelated edit\n')
        with self.assertRaisesRegex(ValueError,'recovery_requires_review'):self.apply()
        self.assertEqual(self.old.read_bytes(),b'teacher unrelated edit\n')
    def test_completed_code_change_does_not_claim_success(self):
        with patch.object(remote,'candidate_probe'):self.apply()
        self.old.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'completed_installation_changed'):self.apply()
    def test_backup_corruption_is_not_restored(self):
        self.powerloss();backup=self.install/'queued-update-backups'/self.operation/'before/app/sample.py';backup.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError,'backup_hash_mismatch'):self.apply()
    def test_operation_and_archive_binding(self):
        with patch.object(remote,'candidate_probe'):self.apply()
        with self.assertRaisesRegex(ValueError,'operation_mismatch'):remote.apply(self.install,self.candidate,self.manifest,self.operation,'c'*64)
        with self.assertRaisesRegex(ValueError,'invalid_operation'):remote.apply(self.install,self.candidate,self.manifest,'../../escape',self.archive_hash)
    def test_candidate_modified_after_unpack_is_refused(self):
        (self.candidate/'app/sample.py').write_bytes(b'changed = True\n')
        with self.assertRaisesRegex(ValueError,'candidate_changed_after_unpack'):self.apply()
        self.assertEqual(self.old.read_bytes(),b'old = True\n')
    def test_explicit_embedded_sys_path_and_bad_runtime(self):
        seen=[]
        def invoke(argv,**kwargs):seen.append(argv);return subprocess.CompletedProcess(argv,0,b'',b'')
        with patch.object(remote.subprocess,'run',side_effect=invoke):remote.candidate_probe(Path('runtime/python.exe'),self.candidate)
        self.assertIn('sys.path.insert(0,sys.argv[1])',seen[0][3]);self.assertEqual(seen[0][-1],str(self.candidate));self.assertIn('-B',seen[0])
        with patch.object(remote.subprocess,'run',return_value=subprocess.CompletedProcess([],1,b'',b'')):
            with self.assertRaisesRegex(ValueError,'candidate_runtime_incompatible'):remote.candidate_probe(Path('runtime/python.exe'),self.candidate)

class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.files={'app/laser_station.py':b'x=1\n','app/instance.py':b'x=1\n','app/backup_guard.py':b'x=1\n','web/laserstation.html':b'html','CREDITS.md':b'Klostermarksskolen'}
    def archive(self,extra=None,duplicate_json=False):
        path=self.root/'archive.zip';manifest={'format':remote.FORMAT,'release':'test','files':{n:{'sha256':hashlib.sha256(v).hexdigest(),'size':len(v)} for n,v in self.files.items()}}
        raw=json.dumps(manifest)
        if duplicate_json:raw=raw.replace('"release": "test"','"release": "unsafe", "release": "test"')
        with zipfile.ZipFile(path,'w') as z:
            for name,value in self.files.items():z.writestr(name,value)
            z.writestr('manifest.json',raw)
            if extra:extra(z)
        return path
    def unpack(self,path,expected=None):return remote.unpack(path,expected or remote.digest(path),self.root/'out')
    def test_valid_manifest_unpacks_and_transport_hash_is_required(self):
        path=self.archive();self.assertEqual(self.unpack(path)['release'],'test')
        with self.assertRaisesRegex(ValueError,'archive_hash_mismatch'):self.unpack(path,'0'*64)
    def test_duplicate_json_cannot_hide_manifest_fields(self):
        with self.assertRaisesRegex(ValueError,'duplicate_json_key'):self.unpack(self.archive(duplicate_json=True))
    def test_zip_traversal_windows_aliases_case_duplicates_and_symlink_rejected(self):
        for name in ('../escape','app/../escape','C:/escape','app/CON.py','app/file.','APP/instance.py','app\\escape'):
            with self.subTest(name=name),self.assertRaises(ValueError):self.unpack(self.archive(lambda z:z.writestr(name,b'x')))
        def symlink(z):
            info=zipfile.ZipInfo('app/link');info.create_system=3;info.external_attr=(stat.S_IFLNK|0o777)<<16;z.writestr(info,'outside')
        with self.assertRaisesRegex(ValueError,'unsafe_archive'):self.unpack(self.archive(symlink))
    def test_payload_mutation_and_invalid_python_rejected(self):
        path=self.archive();original=zipfile.ZipFile.read
        def read(z,name,*args,**kwargs):return b'bad' if name=='app/instance.py' else original(z,name,*args,**kwargs)
        with patch.object(zipfile.ZipFile,'read',read),self.assertRaisesRegex(ValueError,'payload_hash_mismatch'):self.unpack(path)
        self.files['app/instance.py']=b'invalid python :\n'
        with self.assertRaises(SyntaxError):self.unpack(self.archive())

class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        (self.root/'station-code.zip').write_bytes(b'zip');(self.root/'remote_runner.py').write_bytes(b'runner')
        for name in ('key','known'):(self.root/name).write_bytes(b'fixture')
        self.config={'schema':1,'role':'windows_laser_teacher','release_id':'laser','release':'2026.10.20','target':'laser1','user':'administrator','hosts':['maker.example','192.0.2.7'],'host_key_alias':'192.0.2.7',
            'archive_sha256':queue.digest(self.root/'station-code.zip'),'runner_sha256':queue.digest(self.root/'remote_runner.py'),'ssh_key':str(self.root/'key'),'known_hosts':str(self.root/'known'),
            'install_path':"C:\\Users\\teacher'A\\Laserstation"}
        self.operation='a'*32
    def test_worker_input_bindings_and_no_implicit_profile_root(self):
        queue.validate(self.root,self.config)
        for key,value in [('install_path',None),('hosts',['bad;command']),('user','a@bad'),('target','../x'),('archive_sha256','0'*64)]:
            with self.subTest(key=key),self.assertRaises((ValueError,TypeError)):queue.validate(self.root,{**self.config,key:value})
    def test_reviewed_user_default_is_resolved_remotely_and_not_invented(self):
        config={**self.config,'install_path_policy':'user_default'};config.pop('install_path')
        queue.validate(self.root,config);commands=[]
        def invoke(argv,**kwargs):
            commands.append(argv)
            return subprocess.CompletedProcess(argv,0,b'{"status":"complete"}' if '-EncodedCommand' in argv[-1] else b'maker-update-ready',b'')
        queue.deliver(self.root,config,self.operation,invoke)
        script=base64.b64decode(commands[-1][-1].split()[-1]).decode('utf-16le')
        self.assertIn("$root=Join-Path $env:LOCALAPPDATA 'Laserstation'",script)
        self.assertNotIn('C:\\Users\\administrator',script)
    def test_strict_host_key_and_isolated_ssh_config(self):
        opts=queue.options(self.config)
        for value in ('StrictHostKeyChecking=yes','IdentitiesOnly=yes','BatchMode=yes','HostKeyAlgorithms=ssh-ed25519'):self.assertIn(value,opts)
        self.assertEqual(opts[0:2],['-F','NUL' if os.name=='nt' else '/dev/null']);self.assertIn('HostKeyAlias=192.0.2.7',opts)
    def test_offline_and_timeout_never_report_success_or_transfer(self):
        calls=[]
        def invoke(argv,**kwargs):calls.append(argv);return subprocess.CompletedProcess(argv,255,b'',b'offline')
        self.assertEqual(queue.deliver(self.root,self.config,self.operation,invoke)['status'],'waiting_for_computer');self.assertEqual(len(calls),2)
        def timeout(argv,**kwargs):raise subprocess.TimeoutExpired(argv,20)
        self.assertEqual(queue.deliver(self.root,self.config,self.operation,timeout)['status'],'waiting_for_computer')
    def test_transfer_and_remote_result_checks_and_bound_install_path(self):
        commands=[]
        def invoke(argv,**kwargs):
            commands.append(argv)
            if argv[-1]=='echo maker-update-ready':return subprocess.CompletedProcess(argv,0,b'maker-update-ready\r\n',b'')
            if '-EncodedCommand' in argv[-1]:return subprocess.CompletedProcess(argv,0,b'{"status":"complete"}',b'')
            return subprocess.CompletedProcess(argv,0,b'',b'')
        self.assertEqual(queue.deliver(self.root,self.config,self.operation,invoke)['status'],'complete')
        script=base64.b64decode(commands[-1][-1].split()[-1]).decode('utf-16le')
        self.assertIn(base64.b64encode(self.config['install_path'].encode()).decode(),script);self.assertNotIn("Join-Path $env:LOCALAPPDATA 'Laserstation'",script)
        self.assertIn(self.config['archive_sha256'],script);self.assertIn(self.config['runner_sha256'],script)
        with self.assertRaises(ValueError):queue.deliver(self.root,self.config,'bad-operation',invoke)
    def test_nonzero_exit_cannot_be_complete_and_failed_transfer_retries(self):
        def invoke(argv,**kwargs):
            if argv[-1]=='echo maker-update-ready':return subprocess.CompletedProcess(argv,0,b'maker-update-ready',b'')
            if '-EncodedCommand' in argv[-1]:return subprocess.CompletedProcess(argv,1,b'{"status":"complete"}',b'')
            return subprocess.CompletedProcess(argv,0,b'',b'')
        self.assertEqual(queue.deliver(self.root,self.config,self.operation,invoke)['status'],'blocked')
        def transfer_failure(argv,**kwargs):return subprocess.CompletedProcess(argv,0,b'maker-update-ready',b'') if argv[-1]=='echo maker-update-ready' else subprocess.CompletedProcess(argv,1,b'',b'')
        self.assertEqual(queue.deliver(self.root,self.config,self.operation,transfer_failure)['status'],'retry')
    def test_heartbeat_runs_when_no_queued_operation(self):
        class Service:
            def __init__(self):self.beats=[]
            def heartbeat(self,r):self.beats.append(r)
            def worker_allowed(self,r,t):return False
        service=Service();queue.tick(self.root,self.config,service,{});self.assertEqual(service.beats,['laser'])

class ActualSourceBuildTests(unittest.TestCase):
    def test_real_build_unpack_and_verified_13_embedded_runtime_candidate(self):
        runtime=build.ROOT.parent/'Makervaerkstedet/Makervaerkstedet-Windows-20261013/runtime/python.exe'
        # Explicit local host evidence, not a portable test's fallback to global Python.
        runtime=Path(os.environ.get('MAKER_TEST_EMBEDDED_RUNTIME',str(runtime)))
        self.assertTrue(runtime.is_file(),'Set MAKER_TEST_EMBEDDED_RUNTIME to verified embedded CPython 3.14.7')
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);receipt=build.build(root/'build');candidate=root/'candidate'
            manifest=remote.unpack(root/'build/station-code.zip',receipt['archive_sha256'],candidate)
            self.assertIn('app/laser_station_install.py',manifest['files']);self.assertIn('web/megavaerkstedet.js',manifest['files'])
            before=remote.digest(runtime);remote.candidate_probe(runtime,candidate);self.assertEqual(remote.digest(runtime),before)
            self.assertFalse(list(candidate.rglob('__pycache__')))

if __name__=='__main__':unittest.main()
