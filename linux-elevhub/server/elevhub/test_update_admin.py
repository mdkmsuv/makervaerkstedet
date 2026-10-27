"""New durable update queue feature: isolated tests, no SSH or installation."""
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from update_admin import UpdateAdministration, Conflict, version_key
from elevhub import Server, Laerer
from tilstand import Laererkode, Tilstand

class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.now=[1000.0]
        self.s=UpdateAdministration(self.root,clock=lambda:self.now[0])
        self.worker=self.root/'worker';self.worker.mkdir();self.write(self.worker/'config.json',{'release':'2026.10.13'})
        self.identity=self.root/'identity.json';self.write(self.identity,{'enheder':{'pc1':{'token_sha256':'a'*64}}})
        self.key=self.root/'key';self.key.write_text('private fixture key')
        self.known=self.root/'known';self.known.write_text('pc1 ssh-ed25519 AAAATEST\nlaser1 ssh-ed25519 AAAATEST\n')
        self.r={'id':'linux-full','role':'linux_student','version':'2026.10.13','reviewed':True,'worker_root':str(self.worker),
                'manifest_sha256':hashlib.sha256((self.worker/'config.json').read_bytes()).hexdigest()}
        self.t={'id':'pc1','name':'Elevcomputer 1','role':'linux_student','worker_target':'pc1','release_ids':['linux-full'],
                'identity_file':str(self.identity),'identity_sha256':'a'*64,'ssh_key':str(self.key),'known_hosts':str(self.known)}
        self.configure()
    @staticmethod
    def write(path,data):path.write_text(json.dumps(data),encoding='utf-8')
    def configure(self):
        self.write(self.s.root/'catalog.json',{'schema':1,'releases':[self.r]})
        self.write(self.s.root/'targets.json',{'schema':1,'targets':[self.t]})
    def cmd(self,action='enqueue',params=None,request='1'*32):
        return {'request_id':request,'action':action,'params':params or {'release_id':self.r['id'],'target_ids':[self.t['id']]}}
    def add(self):return self.s.execute('teacher:test',self.cmd())
    def job(self):return self.s.snapshot()['targets'][0]['job']
    def test_zero_clients(self):
        self.write(self.s.root/'catalog.json',{'schema':1,'releases':[]});self.write(self.s.root/'targets.json',{'schema':1,'targets':[]})
        self.assertEqual(self.s.snapshot()['targets'],[]);self.assertFalse(self.s.snapshot()['configured'])
    def test_durable_idempotent_enqueue_and_actor_conflict(self):
        self.assertEqual(self.add(),self.add());self.assertEqual(len(self.s.snapshot()['audit']),1)
        self.assertEqual(UpdateAdministration(self.root).snapshot()['targets'][0]['job']['status'],'pending')
        with self.assertRaises(Conflict):self.s.execute('teacher:other',self.cmd())
    def test_browser_paths_and_commands_are_not_an_api(self):
        for name in ('path','command','script','host','ssh_key','upload','worker_root'):
            with self.subTest(name=name),self.assertRaises(ValueError):
                self.s.execute('teacher',self.cmd(params={'release_id':self.r['id'],'target_ids':['pc1'],name:'arbitrary'}))
        with self.assertRaises(ValueError):self.s.execute('teacher',self.cmd(params={'release_id':'../../config','target_ids':['pc1']}))
    def test_manifest_change_and_registry_presence_without_enrollment_rejected(self):
        (self.worker/'config.json').write_text('changed')
        with self.assertRaises(ValueError):self.add()
        self.r['manifest_sha256']=hashlib.sha256(b'changed').hexdigest();self.configure()
        self.write(self.identity,{'enheder':{'pc1':{'name':'pc1'}}})
        with self.assertRaises(ValueError):self.add()
        self.assertIsNone(self.job())
    def test_atomic_batch(self):
        with self.assertRaises(ValueError):self.s.execute('teacher',self.cmd(params={'release_id':self.r['id'],'target_ids':['pc1','missing']}))
        self.assertIsNone(self.job())
    def test_pause_resume_offline_and_worker_lease(self):
        r=self.r['id'];self.assertFalse(self.s.worker_allowed(r,'pc1'));self.add();j=self.job()
        self.s.execute('teacher',self.cmd('pause',{'target_id':'pc1','version':j['version']},'2'*32));self.assertFalse(self.s.worker_allowed(r,'pc1'))
        j=self.job();self.s.execute('teacher',self.cmd('resume',{'target_id':'pc1','version':j['version']},'3'*32))
        self.assertTrue(self.s.worker_allowed(r,'pc1'));self.assertFalse(UpdateAdministration(self.root,clock=lambda:self.now[0]).worker_allowed(r,'pc1'))
        self.s.report(r,'pc1','waiting_for_computer');self.assertEqual(self.job()['status'],'offline')
    def test_changed_identity_blocks_before_worker_dispatch(self):
        self.add();self.write(self.identity,{'enheder':{}});self.assertFalse(self.s.worker_allowed(self.r['id'],'pc1'));self.assertEqual(self.job()['status'],'blocked')
    def test_staged_is_incomplete_until_restart_receipt(self):
        self.add();r=self.r['id'];self.assertTrue(self.s.worker_allowed(r,'pc1'));self.s.report(r,'pc1','staged_awaiting_restart')
        self.assertEqual(self.job()['status'],'staged_awaiting_restart');self.assertTrue(self.s.worker_allowed(r,'pc1'));self.s.report(r,'pc1','complete')
        self.assertEqual(self.job()['status'],'completed');self.assertFalse(self.s.worker_allowed(r,'pc1'))
    def test_stale_version_and_live_retry_rejected(self):
        self.add();j=self.job()
        with self.assertRaises(Conflict):self.s.execute('teacher',self.cmd('pause',{'target_id':'pc1','version':0},'2'*32))
        self.s.worker_allowed(self.r['id'],'pc1')
        with self.assertRaises(Conflict):self.s.execute('teacher',self.cmd('retry',{'target_id':'pc1','version':j['version']},'2'*32))
    def test_expired_lease_retry_rejects_old_worker_report(self):
        self.add();r=self.r['id'];self.s.worker_allowed(r,'pc1');self.now[0]+=10801;j=self.job()
        self.s.execute('teacher',self.cmd('retry',{'target_id':'pc1','version':j['version']},'2'*32));self.s.report(r,'pc1','complete')
        self.assertEqual(self.job()['status'],'pending')
    def test_returning_worker_reclaims_expired_attempt_without_teacher_reset(self):
        self.add();release=self.r['id']
        self.assertTrue(self.s.worker_allowed(release,'pc1'))
        self.s.report(release,'pc1','applying')
        original_job=self.job()['id']
        self.now[0]+=10801
        self.assertTrue(self.s.worker_allowed(release,'pc1'))
        self.s.report(release,'pc1','staged_awaiting_restart')
        self.assertEqual(self.job()['status'],'staged_awaiting_restart')
        self.assertEqual(self.job()['id'],original_job)
        self.assertEqual(self.job()['attempt'],2)
        self.assertTrue(self.s.worker_allowed(release,'pc1'))
        self.s.report(release,'pc1','complete',installed_version='2026.10.13')
        self.assertEqual(self.job()['status'],'completed')
    def test_expired_owner_cannot_report_after_another_worker_reclaims(self):
        self.add();release=self.r['id']
        self.assertTrue(self.s.worker_allowed(release,'pc1'))
        self.now[0]+=10801
        other=UpdateAdministration(self.root,clock=lambda:self.now[0])
        self.assertTrue(other.worker_allowed(release,'pc1'))
        self.assertFalse(self.s.worker_allowed(release,'pc1'))
        self.s.report(release,'pc1','complete')
        self.assertNotEqual(self.job()['status'],'completed')
        other.report(release,'pc1','staged_awaiting_restart')
        self.assertEqual(self.job()['status'],'staged_awaiting_restart')
    def test_laser_pinned_host_is_not_claimed_as_verified_role(self):
        self.r.update(id='laser',role='windows_laser_teacher');self.t.update(id='laser1',worker_target='laser1',role='windows_laser_teacher',release_ids=['laser']);self.configure()
        self.assertFalse(self.s.snapshot()['targets'][0]['role_verified']);self.add();self.assertTrue(self.s.worker_allowed('laser','laser1'));self.s.report('laser','laser1','complete')
        self.assertEqual(self.job()['status'],'blocked');j=self.job();self.s.execute('teacher',self.cmd('retry',{'target_id':'laser1','version':j['version']},'2'*32))
        self.assertTrue(self.s.worker_allowed('laser','laser1'));self.s.report('laser','laser1','complete',role_verified=True)
        self.assertTrue(self.s.snapshot()['targets'][0]['role_verified'])
    def test_exactly_one_worker_claims_atomically(self):
        self.add();services=[UpdateAdministration(self.root,clock=lambda:self.now[0]) for _ in range(8)]
        with ThreadPoolExecutor(8) as pool:result=list(pool.map(lambda s:s.worker_allowed(self.r['id'],'pc1'),services))
        self.assertEqual(sum(result),1)
    def test_linux_first_key_requires_explicit_enrollment_proof_policy(self):
        self.known.unlink()
        with self.assertRaises(ValueError):self.add()
        self.t['host_key_policy']='enrollment_proof';self.configure();self.add()
        self.assertEqual(self.s.snapshot()['targets'][0]['credentials_mode'],'enrollment_proof')
        self.write(self.identity,{'enheder':{}})
        self.assertFalse(self.s.worker_allowed(self.r['id'],'pc1'))
    def test_system_attestation_works_when_http_cannot_read_worker_or_stat_key(self):
        self.s.heartbeat(self.r['id'])
        actual_stat = Path.stat
        def stat_file(path,*args,**kwargs):
            if path==self.key: raise PermissionError('SYSTEM-only private key')
            return actual_stat(path,*args,**kwargs)
        with patch.object(Path,'read_bytes',side_effect=PermissionError('SYSTEM-only worker config')),patch.object(Path,'stat',stat_file):
            self.assertTrue(self.s.snapshot()['releases'][0]['ready'])
            self.assertTrue(self.s.snapshot()['targets'][0]['credentials_ready'])
            self.add()
            # Privileged worker must still perform direct checks before SSH.
            self.assertFalse(self.s.worker_allowed(self.r['id'],'pc1'))
    def test_readiness_attestation_is_bound_config_and_expires(self):
        self.s.heartbeat(self.r['id'])
        with patch.object(Path,'read_bytes',side_effect=PermissionError('SYSTEM-only worker config')):
            self.assertTrue(self.s.release_ready(self.r))
            self.assertFalse(self.s.release_ready({**self.r,'manifest_sha256':'b'*64}))
            self.now[0]+=86401
            self.assertFalse(self.s.release_ready(self.r))
    def test_snapshot_redacts_paths_and_secrets(self):
        self.add();data=json.dumps(self.s.snapshot())
        for secret in (str(self.root),'private fixture key','a'*64,'ssh_key','token_sha256'):self.assertNotIn(secret,data)

    def test_versions_are_evidence_only_after_completion_and_survive_restart(self):
        self.add();r=self.r['id'];self.s.worker_allowed(r,'pc1')
        self.s.report(r,'pc1','staged_awaiting_restart')
        self.assertIsNone(self.s.snapshot()['targets'][0]['last_verified_version'])
        self.s.worker_allowed(r,'pc1');self.s.report(r,'pc1','complete',installed_version='2026.10.13')
        target=UpdateAdministration(self.root).snapshot()['targets'][0]
        self.assertEqual(target['last_verified_version'],'2026.10.13')
        self.assertEqual(target['last_verified_at'],self.now[0])
        self.t['identity_sha256']='b'*64;self.configure()
        self.assertIsNone(self.s.snapshot()['targets'][0]['last_verified_version'])

    def test_wrong_completed_version_or_package_never_records_installation(self):
        for values in ({'installed_version':'2026.10.12'},{'package_sha256':'b'*64}):
            self.add();self.s.worker_allowed(self.r['id'],'pc1')
            self.s.report(self.r['id'],'pc1','complete',**values)
            self.assertEqual(self.job()['status'],'blocked')
            self.assertIsNone(self.s.snapshot()['targets'][0]['last_verified_version'])
            with self.s.connect() as db:db.execute('DELETE FROM jobs');db.execute('DELETE FROM requests')

    def test_expired_completion_and_changed_package_are_not_evidence(self):
        self.add();self.s.worker_allowed(self.r['id'],'pc1');self.now[0]+=10801
        self.s.report(self.r['id'],'pc1','complete')
        self.assertIsNone(self.s.snapshot()['targets'][0]['last_verified_version'])
        self.assertNotEqual(self.job()['status'],'completed')
        other=UpdateAdministration(self.root,clock=lambda:self.now[0]);other.worker_allowed(self.r['id'],'pc1')
        (self.worker/'config.json').write_text('changed after claim')
        other.report(self.r['id'],'pc1','complete')
        self.assertEqual(self.job()['status'],'blocked')

    def test_completion_rechecks_credentials_even_with_fresh_readiness_attestation(self):
        self.s.heartbeat(self.r['id']);self.add();self.s.worker_allowed(self.r['id'],'pc1')
        self.key.unlink()
        self.assertTrue(self.s.credentials(self.t),'HTTP readiness may use the earlier attestation')
        self.s.report(self.r['id'],'pc1','complete',installed_version='2026.10.13')
        self.assertEqual(self.job()['status'],'blocked')
        self.assertIsNone(self.s.snapshot()['targets'][0]['last_verified_version'])

    def test_new_release_preserves_evidence_and_rejects_downgrade(self):
        self.add();self.s.worker_allowed(self.r['id'],'pc1');self.s.report(self.r['id'],'pc1','complete')
        old=self.r.copy()
        for version in ('2026.10.12','2026.10.13','2026.10.14'):
            self.r={**old,'id':'next','version':version}
            self.t['release_ids']=['linux-full','next']
            self.write(self.s.root/'catalog.json',{'schema':1,'releases':[old,self.r]})
            self.write(self.s.root/'targets.json',{'schema':1,'targets':[self.t]})
            self.assertEqual(self.s.snapshot()['targets'][0]['last_verified_version'],'2026.10.13')
            command=self.cmd(request='2'*32)
            if version!='2026.10.14':
                with self.assertRaises(Conflict):self.s.execute('teacher:test',command)
            else:
                self.s.execute('teacher:test',command)
                self.assertEqual(self.job()['status'],'pending')

    def test_numeric_build_order_and_invalid_version(self):
        self.assertLess(version_key('2026.10.20.2'),version_key('2026.10.20.10'))
        self.assertEqual(version_key('2026.10.20'),version_key('2026.10.20.0'))
        self.assertIsNone(version_key('2026.02.30'));self.assertIsNone(version_key('unknown'))

    def test_version_preflight_blocks_keep_public_reason_without_installation_evidence(self):
        self.add()
        for index,code in enumerate(('version_unknown','version_not_older')):
            if index:
                self.s.execute('teacher',self.cmd('retry',{'target_id':'pc1','version':self.job()['version']},'2'*32))
            self.assertTrue(self.s.worker_allowed(self.r['id'],'pc1'))
            self.s.report(self.r['id'],'pc1','blocked',code=code)
            self.assertEqual(self.job()['code'],code)
            self.assertEqual(self.job()['status'],'blocked')
            self.assertIsNone(self.s.snapshot()['targets'][0]['last_verified_version'])

    def test_local_server_has_no_ssh_requirement_but_needs_controller_and_role_proof(self):
        controller=self.root/'controller.json';self.write(controller,{'layout':'school-flat-v1'})
        self.r.update(id='server-release',role='windows_server')
        self.t={'id':'server','name':'Hovedserver','role':'windows_server','worker_target':'server',
                'release_ids':['server-release'],'transport':'local_server','controller_config':str(controller),
                'controller_sha256':hashlib.sha256(controller.read_bytes()).hexdigest()}
        self.configure()
        target=self.s.snapshot()['targets'][0]
        self.assertEqual(target['credentials_mode'],'local_server');self.assertFalse(target['role_verified'])
        self.add();self.s.worker_allowed('server-release','server');self.s.report('server-release','server','complete')
        self.assertEqual(self.job()['status'],'blocked')
        self.s.execute('teacher',self.cmd('retry',{'target_id':'server','version':self.job()['version']},'2'*32))
        self.s.worker_allowed('server-release','server')
        self.s.report('server-release','server','complete',role_verified=True,installed_version='2026.10.13')
        self.assertEqual(self.job()['status'],'completed')
        self.assertEqual(self.s.snapshot()['targets'][0]['last_verified_version'],'2026.10.13')
        controller.write_text('changed')
        self.assertFalse(self.s.direct_credentials(self.t))

class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);root=Path(self.tmp.name)
        self.server=Server(('127.0.0.1',0),Laerer);self.server.navn='laerer';self.server.kode=Laererkode(root);self.server.kode.vaelg('teacher-test-password')
        self.server.tilstand=Tilstand(root,[])
        self.server.update_administration=UpdateAdministration(root);threading.Thread(target=self.server.serve_forever,daemon=True).start();self.addCleanup(self.close)
        self.origin='http://127.0.0.1:'+str(self.server.server_port)
    def close(self):self.server.shutdown();self.server.server_close()
    def cookie(self):return {'Cookie':'elevhub_laerer='+self.server.kode.log_ind('teacher-test-password','127.0.0.1')}
    def call(self,path,body=None,headers=None):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        c.request('POST' if body is not None else 'GET',path,json.dumps(body) if body is not None else None,{'Content-Type':'application/json',**(headers or {})})
        reply=c.getresponse();data=reply.read();c.close();return reply.status,json.loads(data)
    def test_teacher_cookie_required_bearer_rejected(self):
        self.assertEqual(self.call('/api/updates/state')[0],401)
        self.assertEqual(self.call('/api/updates/state',headers={**self.cookie(),'Authorization':'Bearer forged'})[0],403)
        self.assertEqual(self.call('/api/updates/state',headers=self.cookie())[0],200)
    def test_mutations_require_same_origin_and_json(self):
        h=self.cookie();body={'request_id':'1'*32,'action':'enqueue','params':{'path':'C:/arbitrary'}}
        self.assertEqual(self.call('/api/updates/commands',body,h)[0],403)
        self.assertEqual(self.call('/api/updates/commands',body,{**h,'Origin':'https://evil.test'})[0],403)
        self.assertEqual(self.call('/api/updates/commands',body,{**h,'Origin':self.origin})[0],400)
        self.assertEqual(self.call('/api/updates/commands',body,{**h,'Origin':self.origin,'Content-Type':'text/plain'})[0],415)

if __name__=='__main__':unittest.main()
