"""Neutral isolated release feed tests. No GitHub account, network or machines."""
from copy import deepcopy
import hashlib
import http.client
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from release_feed import (ReleaseFeed,ReleaseFeedRunner,FeedInvalid,FeedConflict,GitHubRedirect,
                          release_manifest,trusted_url,MAX_METADATA,MAX_PACKAGE)
from elevhub import Server,Laerer
from tilstand import Laererkode, Tilstand


REPOSITORY='example/project'
BASE='https://github.com/'+REPOSITORY+'/releases/download/v2026.10.20/'


class Response(io.BytesIO):
    def __init__(self,data,url):super().__init__(data);self.headers={'Content-Length':str(len(data))};self.url=url;self.status=200
    def geturl(self):return self.url


class FeedTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.now=[100000.0];self.calls=[];self.counter=0
        self.payload=b'package fixture; never executable'
        self.package={'role':'linux_student','asset':'linux.zip','format':'linux-queue-v1','sha256':hashlib.sha256(self.payload).hexdigest(),'size':len(self.payload)}
        self.manifest={'schema':1,'product':'Makervaerkstedet','version':'2026.10.20','notes':'Release notes <script>not executable</script>','packages':[self.package]}
        self.metadata={'id':7,'tag_name':'v2026.10.20','draft':False,'prerelease':False,'assets':[
            {'name':'makervaerkstedet-update.json','state':'uploaded','size':len(json.dumps(self.manifest).encode()),'browser_download_url':BASE+'makervaerkstedet-update.json'},
            {'name':'linux.zip','state':'uploaded','size':len(self.payload),'browser_download_url':BASE+'linux.zip'}]}
        self.feed=ReleaseFeed(self.root,clock=lambda:self.now[0],opener=self.open)
    def open(self,url,repository):
        self.calls.append((url,repository))
        if url.endswith('/releases/latest'):data=json.dumps(self.metadata).encode()
        elif url.endswith('makervaerkstedet-update.json'):data=json.dumps(self.manifest).encode()
        else:data=self.payload
        return Response(data,url)
    def command(self,action,params):
        self.counter+=1
        return {'request_id':f'{self.counter:032x}','action':action,'params':params}
    def configure(self):return self.feed.execute('teacher:test',self.command('configure',{'repository':REPOSITORY}))
    def discover(self):self.configure();self.feed.tick(self.root/'stage')
    def download(self):
        return self.feed.execute('teacher:test',self.command('download',{'release_id':7,'asset':'linux.zip'}))
    def test_unconfigured_feed_is_honest_and_never_connects(self):
        self.feed.tick(self.root/'stage');self.assertEqual(self.calls,[])
        self.assertEqual(self.feed.snapshot()['state'],'not_configured');self.assertIsNone(self.feed.snapshot()['latest'])
    def test_repository_setup_is_one_time_idempotent_and_has_no_custom_urls(self):
        command=self.command('configure',{'repository':REPOSITORY})
        self.assertEqual(self.feed.execute('teacher:test',command),self.feed.execute('teacher:test',command))
        with self.assertRaises(FeedConflict):self.feed.execute('teacher:other',command)
        with self.assertRaises(FeedConflict):self.feed.execute('teacher',self.command('configure',{'repository':'other/project'}))
        for value in ('https://github.com/example/project','example/project/../other','http://192.0.2.1/private','owner;command/project'):
            with self.subTest(value=value),self.assertRaises(FeedInvalid):self.feed.execute('teacher',self.command('configure',{'repository':value}))
    def test_worker_checks_every_day_independently_of_browser(self):
        self.discover();self.assertEqual(len(self.calls),2);self.assertEqual(self.feed.snapshot()['state'],'available')
        self.now[0]+=24*60*60-1;self.feed.tick(self.root/'stage');self.assertEqual(len(self.calls),2)
        self.now[0]+=1;self.feed.tick(self.root/'stage');self.assertEqual(len(self.calls),4)
        self.feed.execute('teacher',self.command('check',{}));self.feed.tick(self.root/'stage');self.assertEqual(len(self.calls),6)
    def test_network_and_404_do_not_invent_availability(self):
        self.configure()
        with patch.object(self.feed,'opener',side_effect=urllib.error.URLError('private local error detail')):self.feed.tick(self.root/'stage')
        snapshot=self.feed.snapshot();self.assertEqual(snapshot['error'],'network_unavailable');self.assertIsNone(snapshot['latest']);self.assertNotIn('private',json.dumps(snapshot))
        self.feed.execute('teacher',self.command('check',{}))
        with patch.object(self.feed,'opener',side_effect=urllib.error.HTTPError('url',404,'not found',{},None)):self.feed.tick(self.root/'stage')
        self.assertEqual(self.feed.snapshot()['error'],'no_release');self.assertIsNone(self.feed.snapshot()['latest'])
    def test_network_failure_keeps_labelled_last_known_release(self):
        self.discover();self.feed.execute('teacher',self.command('check',{}))
        with patch.object(self.feed,'opener',side_effect=TimeoutError):self.feed.tick(self.root/'stage')
        self.assertEqual(self.feed.snapshot()['state'],'check_failed');self.assertEqual(self.feed.snapshot()['latest']['version'],'2026.10.20')
    def test_draft_and_prerelease_are_never_advertised(self):
        for field in ('draft','prerelease'):
            with self.subTest(field=field),self.assertRaises(FeedInvalid):release_manifest({**self.metadata,field:True},self.manifest,REPOSITORY)
    def test_manifest_role_format_version_and_exact_asset_binding(self):
        for changes in ({'format':'school-server-v1'},{'role':'windows_pupil'},{'asset':'../escape.zip'},{'asset':'CON.zip'},{'sha256':'0'*63},{'size':True},{'url':'https://192.0.2.1/payload'}):
            manifest=deepcopy(self.manifest);manifest['packages'][0].update(changes)
            with self.subTest(changes=changes),self.assertRaises(FeedInvalid):release_manifest(self.metadata,manifest,REPOSITORY)
        for version in ('2026.02.30','v2026.10.20','../../release'):
            with self.subTest(version=version),self.assertRaises(FeedInvalid):release_manifest(self.metadata,{**self.manifest,'version':version},REPOSITORY)
        metadata=deepcopy(self.metadata);metadata['assets'][1]['browser_download_url']='https://github.com/other/project/releases/download/v2026.10.20/linux.zip'
        with self.assertRaises(FeedInvalid):release_manifest(metadata,self.manifest,REPOSITORY)
    def test_duplicate_roles_or_asset_names_rejected(self):
        manifest=deepcopy(self.manifest);manifest['packages'].append(manifest['packages'][0])
        with self.assertRaises(FeedInvalid):release_manifest(self.metadata,manifest,REPOSITORY)
        metadata=deepcopy(self.metadata);metadata['assets'].append(metadata['assets'][0])
        with self.assertRaises(FeedInvalid):release_manifest(metadata,self.manifest,REPOSITORY)
    def test_metadata_bound_and_duplicate_json_rejected(self):
        self.configure()
        for content in (b'x'*(MAX_METADATA+1),b'{"id":1,"id":2}'):
            self.feed.execute('teacher',self.command('check',{}))
            with patch.object(self.feed,'opener',side_effect=lambda u,r:Response(content,u)):self.feed.tick(self.root/'stage')
            self.assertEqual(self.feed.snapshot()['error'],'invalid_release')
    def test_download_requires_selected_published_asset_and_no_paths(self):
        self.discover()
        for params in ({'release_id':7,'asset':'../outside.zip'},{'release_id':8,'asset':'linux.zip'},{'release_id':7,'asset':'linux.zip','path':'C:/outside'}):
            with self.subTest(params=params),self.assertRaises(FeedInvalid):self.feed.execute('teacher',self.command('download',params))
        self.assertEqual(self.feed.ready_packages(),[])
    def test_download_streaming_hash_size_staging_and_no_autoexecute(self):
        self.discover();command=self.command('download',{'release_id':7,'asset':'linux.zip'})
        result=self.feed.execute('teacher',command);self.assertEqual(self.feed.execute('teacher',command),result)
        self.assertEqual(len(self.calls),2);self.feed.tick(self.root/'stage');ready=self.feed.ready_packages()
        self.assertEqual(len(ready),1);self.assertEqual(Path(ready[0]['path']).read_bytes(),self.payload)
        self.assertEqual(ready[0]['format'],'linux-queue-v1');self.assertEqual(ready[0]['version'],'2026.10.20')
        public=json.dumps(self.feed.snapshot());self.assertNotIn(str(self.root),public);self.assertNotIn('https://',public)
        self.feed.mark_outcome(result['download_id'],'imported');self.assertEqual(self.feed.ready_packages(),[])
        self.assertEqual(self.feed.snapshot()['downloads'][0]['status'],'imported')
    def test_wrong_size_or_hash_is_failed_and_partial_removed(self):
        self.discover();original=self.payload
        for bad in (b'wrong',b'x'*len(original)):
            self.download();self.payload=bad
            self.feed.tick(self.root/'stage');self.assertEqual(self.feed.ready_packages(),[])
            self.assertEqual(self.feed.snapshot()['downloads'][0]['status'],'failed');self.assertEqual(list((self.root/'stage').rglob('*.part')),[])
        self.payload=original;self.download();self.feed.tick(self.root/'stage');self.assertEqual(len(self.feed.ready_packages()),1)
    def test_worker_restart_recovers_incomplete_download(self):
        self.discover();self.download()
        with self.feed.connect() as db:db.execute("UPDATE downloads SET status='downloading'")
        restarted=ReleaseFeed(self.root,clock=lambda:self.now[0],opener=self.open);restarted.tick(self.root/'stage')
        self.assertEqual(len(restarted.ready_packages()),1)
    def test_existing_update_jobs_database_is_untouched(self):
        original=b'opaque old queue fixture';path=self.feed.root/'queue.sqlite3';path.write_bytes(original)
        self.discover();self.download();self.feed.tick(self.root/'stage');self.assertEqual(path.read_bytes(),original)
    def test_strict_redirects_never_allow_http_credentials_private_hosts_or_other_repo(self):
        request=urllib.request.Request(BASE+'linux.zip')
        for url in ('http://github.com/example/project/releases/download/v/linux.zip','https://api.github.com.evil.invalid/','https://127.0.0.1/private','https://192.0.2.1/private','https://'+'user'+'@'+'github.com/example/project/releases/download/v/linux.zip','https://github.com/other/project/releases/download/v/linux.zip','https://github.com:444/example/project/releases/download/v/linux.zip'):
            with self.subTest(url=url),self.assertRaises(FeedInvalid):GitHubRedirect(REPOSITORY).redirect_request(request,None,302,'',{},url)
        self.assertEqual(trusted_url('https://release-assets.githubusercontent.com/github-production-release-asset/fixture?signed=1',REPOSITORY),'https://release-assets.githubusercontent.com/github-production-release-asset/fixture?signed=1')
    def test_one_worker_holds_network_delivery_lock(self):
        with self.feed.worker_lock():
            with self.assertRaises(OSError):
                with ReleaseFeed(self.root).worker_lock():pass
    def test_shared_database_row_traversal_and_changed_package_fields_are_blocked(self):
        self.discover();self.download()
        with self.feed.connect() as db:
            original=dict(db.execute('SELECT * FROM downloads').fetchone())
        for column,value in (('id','..'),('asset','../../outside.zip'),('repository','other/project'),('package',json.dumps({'url':'https://192.0.2.1/private'}))):
            with self.subTest(column=column):
                with self.feed.connect() as db:
                    db.execute('DELETE FROM downloads')
                    row={**original,column:value,'status':'queued'}
                    db.execute('INSERT INTO downloads(id,repository,release_id,asset,package,status,updated) VALUES(?,?,?,?,?,?,?)',tuple(row[k] for k in ('id','repository','release_id','asset','package','status','updated')))
                self.feed.tick(self.root/'stage')
                self.assertEqual(self.feed.ready_packages(),[])
                self.assertFalse((self.root/'outside.zip').exists())
                self.assertFalse((self.root/'stage').exists())
    def test_ready_packages_ignores_untrusted_path_column_and_rehashes_bytes(self):
        self.discover();self.download();self.feed.tick(self.root/'stage')
        actual=self.feed.ready_packages()[0]['path']
        with self.feed.connect() as db:db.execute("UPDATE downloads SET path='C:/outside/secret.zip'")
        self.assertEqual(self.feed.ready_packages()[0]['path'],actual)
        Path(actual).write_bytes(b'changed after download')
        self.assertEqual(self.feed.ready_packages(),[])
        self.assertEqual(self.feed.snapshot()['downloads'][0]['status'],'blocked')
    def test_ready_packages_requires_trusted_staging_root_after_process_restart(self):
        self.discover();self.download();self.feed.tick(self.root/'stage')
        restarted=ReleaseFeed(self.root,opener=self.open)
        with self.assertRaisesRegex(FeedInvalid,'staging_root_required'):restarted.ready_packages()
        self.assertEqual(len(restarted.ready_packages(self.root/'stage')),1)
    def test_large_single_github_asset_and_nonstring_roles_rejected(self):
        self.assertEqual(MAX_PACKAGE,2*1024**3)
        for changes in ({'size':MAX_PACKAGE},{'size':MAX_PACKAGE+1},{'role':[]},{'role':{}},{'asset':False}):
            manifest=deepcopy(self.manifest);manifest['packages'][0].update(changes)
            with self.subTest(changes=changes),self.assertRaises(FeedInvalid):release_manifest(self.metadata,manifest,REPOSITORY)
    def test_standalone_cli_once_runs_without_browser_or_repository(self):
        root=self.root/'standalone';staging=self.root/'standalone-stage'
        result=subprocess.run([sys.executable,'-B',str(Path(__file__).with_name('release_feed.py')),
            '--data-root',str(root),'--staging-root',str(staging),'--once'],capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse(staging.exists())
        self.assertEqual(ReleaseFeed(root).snapshot()['state'],'not_configured')
    def test_streaming_without_content_length_still_refuses_overrun(self):
        self.discover();self.download()
        def overflowing(url,repository):
            response=Response(self.payload+b'overrun',url);response.headers={};return response
        with patch.object(self.feed,'opener',side_effect=overflowing):self.feed.tick(self.root/'stage')
        self.assertEqual(self.feed.ready_packages(),[])
        self.assertEqual(list((self.root/'stage').rglob('*.part')),[])
    def test_hub_runner_starts_stops_and_never_calls_github_when_unconfigured(self):
        with patch.object(self.feed,'opener',side_effect=AssertionError('No repository means no network')):
            runner=ReleaseFeedRunner(self.feed,self.root/'stage',interval=.01).start()
            self.assertTrue(runner.started_event.wait(1))
            self.assertTrue(runner.running)
            self.assertTrue(runner.stop())
        self.assertFalse(runner.running);self.assertFalse((self.root/'stage').exists())
    def test_hub_runner_discovers_without_any_browser_request(self):
        self.configure();runner=ReleaseFeedRunner(self.feed,self.root/'stage',interval=.01).start()
        try:
            until=time.monotonic()+2
            while self.feed.snapshot()['latest'] is None and time.monotonic()<until:threading.Event().wait(.01)
            self.assertEqual(self.feed.snapshot()['latest']['version'],'2026.10.20')
        finally:self.assertTrue(runner.stop())
    def test_runner_shutdown_is_bounded_and_discards_late_network_result(self):
        self.configure();entered=threading.Event();release=threading.Event()
        def delayed(url,repository):
            entered.set();release.wait(5);return self.open(url,repository)
        self.feed.opener=delayed;runner=ReleaseFeedRunner(self.feed,self.root/'stage').start()
        self.assertTrue(entered.wait(1));started=time.monotonic()
        self.assertFalse(runner.stop(timeout=.05));self.assertLess(time.monotonic()-started,.5)
        release.set();runner.thread.join(1)
        self.assertFalse(runner.thread.is_alive());self.assertIsNone(self.feed.snapshot()['latest'])
        self.assertIsNone(self.feed.snapshot()['last_checked'])
    def test_runner_and_optional_cli_share_lifetime_lock(self):
        runner=ReleaseFeedRunner(self.feed,self.root/'stage').start();self.assertTrue(runner.started_event.wait(1))
        try:
            with self.assertRaises(OSError):
                with ReleaseFeed(self.root).worker_lock('feed-process.lock'):pass
        finally:self.assertTrue(runner.stop())
    def test_runner_cancelled_download_is_not_ready_and_resumes_on_next_process(self):
        self.discover();self.download();entered=threading.Event();release=threading.Event()
        class SlowResponse(Response):
            def read(inner,*args):entered.set();release.wait(5);return super().read(*args)
        self.feed.opener=lambda url,repository:SlowResponse(self.payload,url)
        runner=ReleaseFeedRunner(self.feed,self.root/'stage').start()
        self.assertTrue(entered.wait(1));self.assertFalse(runner.stop(timeout=.05))
        release.set();runner.thread.join(1);self.assertFalse(runner.thread.is_alive())
        self.assertEqual(self.feed.ready_packages(),[]);self.assertEqual(list((self.root/'stage').rglob('*.part')),[])
        restarted=ReleaseFeed(self.root,clock=lambda:self.now[0],opener=self.open);restarted.tick(self.root/'stage')
        self.assertEqual(len(restarted.ready_packages()),1)
    def test_importer_liveness_is_separate_from_download_readiness(self):
        self.discover();self.download();self.feed.tick(self.root/'stage')
        self.assertFalse(self.feed.snapshot()['importer_online'])
        self.feed.importer_heartbeat()
        self.assertTrue(ReleaseFeed(self.root,clock=lambda:self.now[0]).snapshot()['importer_online'])
        self.assertEqual(self.feed.snapshot()['downloads'][0]['status'],'ready')
        self.now[0]+=181
        self.assertFalse(self.feed.snapshot()['importer_online'])
        self.assertEqual(self.feed.snapshot()['downloads'][0]['status'],'ready')
    def test_teacher_can_retry_a_blocked_import_with_new_verified_download(self):
        self.discover();identifier=self.download()['download_id'];self.feed.tick(self.root/'stage')
        self.feed.mark_outcome(identifier,'blocked','local_profile_missing')
        again=self.download()
        self.assertEqual(again['download_id'],identifier)
        self.assertEqual(self.feed.snapshot()['downloads'][0]['status'],'queued')
        self.feed.tick(self.root/'stage')
        self.assertEqual(len(self.feed.ready_packages()),1)


class FeedHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);root=Path(self.temp.name)
        self.server=Server(('127.0.0.1',0),Laerer);self.server.navn='teacher'
        self.server.tilstand=Tilstand(root, [])
        self.server.kode=Laererkode(root);self.server.kode.vaelg('isolated-teacher-code')
        self.server.release_feed=ReleaseFeed(root)
        threading.Thread(target=self.server.serve_forever,daemon=True).start();self.addCleanup(self.close)
        self.origin='http://127.0.0.1:'+str(self.server.server_port)
    def close(self):self.server.shutdown();self.server.server_close()
    def cookie(self):return {'Cookie':'elevhub_laerer='+self.server.kode.log_ind('isolated-teacher-code','127.0.0.1')}
    def call(self,path,body=None,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        connection.request('POST' if body is not None else 'GET',path,json.dumps(body) if body is not None else None,{'Content-Type':'application/json',**(headers or {})})
        response=connection.getresponse();data=response.read();connection.close();return response.status,json.loads(data)
    def test_feed_uses_normal_teacher_auth_and_never_accepts_bearer(self):
        self.assertEqual(self.call('/api/updates/feed/state')[0],401)
        self.assertEqual(self.call('/api/updates/feed/state',headers=self.cookie())[0],200)
        self.assertEqual(self.call('/api/updates/feed/state',headers={**self.cookie(),'Authorization':'Bearer invalid'})[0],403)
    def test_mutation_is_json_origin_guarded_and_only_queues_no_network(self):
        body={'request_id':'a'*32,'action':'configure','params':{'repository':REPOSITORY}};headers=self.cookie()
        self.assertEqual(self.call('/api/updates/feed/commands',body,headers)[0],403)
        self.assertEqual(self.call('/api/updates/feed/commands',body,{**headers,'Origin':'https://other.invalid'})[0],403)
        with patch.object(self.server.release_feed,'opener',side_effect=AssertionError('Browser must never fetch GitHub')):
            self.assertEqual(self.call('/api/updates/feed/commands',body,{**headers,'Origin':self.origin})[0],200)
        self.assertTrue(self.server.release_feed.snapshot()['check_requested'])


if __name__=='__main__':unittest.main()
