"""Durable app actions, loss/restart and exact restricted native removal."""
from pathlib import Path as _LayoutPath
import sys as _layout_sys
_LAYOUT_ROOT = next(p for p in _LayoutPath(__file__).resolve().parents if (p / 'REPOSITORY-LAYOUT.json').is_file())
_layout_sys.path[:0] = [str(_LAYOUT_ROOT), str(_LAYOUT_ROOT / 'system')]
from repository import source_path, repo_root
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = repo_root(__file__)
sys.path[:0] = [str(ROOT), str(source_path('app', ROOT)), str(source_path('linux-elevhub/administration', ROOT))]
from tests.test_app_catalog import envelope
operations = (__import__('app_operations') if importlib.util.find_spec('app_operations') else None)


class NativeBoundary:
    def __init__(self):
        self.version = None; self.state = 'absent'; self.calls = []; self.break_apply = False
    def app_inventory(self, descriptors):
        return [{'id': d['id'], 'package': d['package'], 'version': self.version,
                 'state': self.state} for d in descriptors]
    def refresh(self): self.calls.append('refresh')
    def download(self, artifact, directory):
        p=directory/'app.deb';p.write_bytes(b'synthetic');self.target=artifact['version'];return p
    def install(self, files, rollback):
        self.calls.append('install');self.version=self.target
        self.state='incomplete' if self.break_apply else 'installed'
        if self.break_apply: raise ValueError('dpkg_manual_repair_required')
    def install_app(self,files,descriptor):self.install(files,False)
    def app_matches(self,descriptor):return self.state=='installed' and self.version==descriptor['version']
    def app_absent(self,descriptor):return self.state=='absent'
    def preflight_app(self,descriptor,*,removed=False):return []
    def remove_app(self, descriptor): self.calls.append('remove');self.version=None;self.state='absent'
    def activate(self, core): self.calls.append('activate')
    def activate_app(self,core,descriptor,*,removed=False):self.activate(core)


class AppOperationsTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(operations, 'Independent app actions are missing')
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.native=NativeBoundary()
        self.catalog=envelope();self.catalog['_manifest_sha256']='c'*64
        self.offer={'job_id':'a'*32,'revision':1,'action':'install','app_id':'noter','catalog_sha256':'c'*64}
        self.runner=operations.Executor(self.root,self.native)
    def run_job(self): return self.runner.execute(self.offer,self.catalog,core_version='2.1.0~20261114.1')

    def test_install_uses_exact_catalog_and_replay_does_not_install_twice(self):
        self.assertEqual(self.run_job()['state'],'succeeded')
        self.runner=operations.Executor(self.root,self.native)
        self.assertEqual(self.run_job()['state'],'succeeded')
        self.assertEqual(self.native.calls.count('install'),1)
        self.assertEqual(self.runner.ledger['jobs']['a'*32]['phase'],'complete')
        changed=copy.deepcopy(self.offer);changed['app_id']='laser'
        with self.assertRaisesRegex(ValueError,'job_identity_changed'):
            self.runner.execute(changed,self.catalog,core_version='2.1.0~20261114.1')

    def test_unverified_or_other_catalog_and_unknown_app_rejected_before_native(self):
        for field,value in (('_manifest_sha256','d'*64),('_manifest_sha256',None)):
            cat=copy.deepcopy(self.catalog);cat[field]=value
            with self.assertRaises(ValueError):self.runner.execute(self.offer,cat,core_version='2.1.0~20261114.1')
        self.offer['app_id']='unknown'
        with self.assertRaises(ValueError):self.run_job()
        self.assertEqual(self.native.calls,[])

    def test_remove_reinstall_preserves_projects_outbox_and_highwater(self):
        data=self.root/'pupil-project';data.write_bytes(b'keep pupil bytes')
        outbox=self.root/'pending-submission';outbox.write_bytes(b'keep request id and bytes')
        self.run_job(); water=copy.deepcopy(self.runner.ledger['highwater'])
        self.offer.update(job_id='b'*32,revision=2,action='remove')
        self.assertEqual(self.run_job()['state'],'succeeded')
        self.assertEqual(self.runner.ledger['highwater'],water)
        self.assertEqual(data.read_bytes(),b'keep pupil bytes');self.assertEqual(outbox.read_bytes(),b'keep request id and bytes')
        self.offer.update(job_id='d'*32,revision=3,action='install')
        self.assertEqual(self.run_job()['state'],'succeeded')

    def test_terminal_install_and_remove_cannot_undo_a_newer_action(self):
        first=copy.deepcopy(self.offer);self.run_job()
        self.offer.update(job_id='b'*32,revision=2,action='remove');self.run_job()
        self.runner.execute(first,self.catalog,core_version='2.1.0~20261114.1')
        self.assertEqual(self.native.state,'absent')
        self.offer.update(job_id='d'*32,revision=3,action='install');self.run_job()
        self.runner.execute(dict(first,job_id='e'*32),self.catalog,core_version='2.1.0~20261114.1')
        self.assertEqual(self.native.calls.count('install'),2)

    def test_historical_remove_can_resume_after_other_app_accepts_newer_catalog(self):
        self.run_job()
        newer=envelope();newer['sequence']=2;newer['apps']=[];newer['_manifest_sha256']='d'*64
        from app_catalog import accept_catalog
        self.runner.ledger['highwater']=accept_catalog(newer,self.runner.ledger['highwater']);self.runner.save()
        self.offer.update(job_id='b'*32,revision=2,action='remove')
        self.assertEqual(self.run_job()['state'],'succeeded')
        self.assertEqual(self.runner.ledger['highwater']['sequence'],2)

    def test_manifest_mismatch_and_remaining_manifest_cannot_report_success(self):
        self.native.app_matches=lambda _:False
        self.assertEqual(self.run_job()['code'],'app_installed_manifest_mismatch')
        self.native.app_absent=lambda _:False
        self.offer.update(job_id='b'*32,revision=2,action='remove')
        self.assertEqual(self.run_job()['code'],'app_removal_incomplete')

    def test_interrupted_dpkg_is_durable_repair_and_never_reported_as_removed(self):
        self.native.break_apply=True
        result=self.run_job();self.assertEqual(result['code'],'dpkg_manual_repair_required')
        self.runner=operations.Executor(self.root,self.native)
        self.assertEqual(self.runner.ledger['jobs']['a'*32]['phase'],'repair')
        self.offer.update(job_id='b'*32,revision=2,action='remove')
        self.assertEqual(self.run_job()['code'],'dpkg_manual_repair_required')
        self.assertNotIn('remove',self.native.calls)

    def test_downgrade_and_update_of_absent_app_are_rejected(self):
        self.offer['action']='update'
        self.assertEqual(self.run_job()['code'],'app_not_installed')
        self.native.state='installed';self.native.version='9.0'
        self.offer.update(job_id='b'*32,revision=2)
        self.assertEqual(self.run_job()['code'],'app_downgrade_rejected')

    def test_simulated_removal_must_remove_only_the_selected_app(self):
        app=self.catalog['apps'][0]['descriptor']
        self.assertEqual(operations.removal_plan('Remv makervaerkstedet-app-noter [1.0]\n',app), {'makervaerkstedet-app-noter'})
        for text in ('Remv makervaerkstedet-elev [2.1]\n',
                     'Remv makervaerkstedet-app-noter [1]\nRemv python3 [3]\n',
                     'Remv makervaerkstedet-app-noter [1]\nInst curl (1 Debian)\n'):
            with self.assertRaises(ValueError): operations.removal_plan(text,app)

    def test_native_adapter_rejects_dependency_removal_before_apply(self):
        import apt_client
        app=self.catalog['apps'][0]['descriptor']
        class Restricted(apt_client.Commands):
            def __init__(self): super().__init__([]);self.argv=[]
            def call(self,args,**kw):
                self.argv.append(args)
                return 'Remv makervaerkstedet-app-noter [1]\nRemv python3 [3]\n'
        native=Restricted()
        self.assertTrue(callable(getattr(native,'remove_app',None)), 'Restricted native removal is missing')
        with self.assertRaisesRegex(ValueError,'unsafe_app_removal_plan'):native.remove_app(app)
        self.assertEqual(len(native.argv),1)
        self.assertIn('--simulate',native.argv[0]);self.assertNotIn('purge',native.argv[0])

    def test_native_preflight_rejects_installed_port_or_format_conflict(self):
        import apt_client
        self.assertTrue(callable(getattr(apt_client.Commands,'preflight_app',None)),'Installed-set preflight is missing')
        root=self.root/'apps';folder=root/'alpha';folder.mkdir(parents=True)
        from tests.test_app_catalog import descriptor
        class Inventory(apt_client.Commands):
            def app_inventory(inner,apps):return [{'id':app['id'],'package':app['package'],'state':'installed','version':app['version']} for app in apps]
        native=Inventory([],app_root=root)
        existing=descriptor('alpha',8799,project=False)
        (folder/'app.json').write_text(json.dumps(existing))
        with self.assertRaisesRegex(ValueError,'app_port_conflict'):native.preflight_app(self.catalog['apps'][0]['descriptor'])
        existing=descriptor('alpha',8800)
        (folder/'app.json').write_text(json.dumps(existing))
        with self.assertRaisesRegex(ValueError,'app_project_format_conflict'):native.preflight_app(self.catalog['apps'][0]['descriptor'])
        candidate=descriptor('beta',8891);candidate['project']['extension']='.beta.mkv'
        existing['project']['extension']='.mkv';(folder/'app.json').write_text(json.dumps(existing))
        with self.assertRaisesRegex(ValueError,'app_project_format_conflict'):native.preflight_app(candidate)

    def test_bad_catalog_download_is_reported_and_survives_lost_poll_response(self):
        class Transport:
            fail=False
            def poll_apps(inner,body):
                if inner.fail: raise OSError('offline')
                return {'schema':1,'jobs':[self.offer]}
            def get(inner,path,limit): return b'bad catalog bytes'
        native=self.native
        native.installed=lambda:[{'component':'studentcore','version':'2.1.0~20261114.1'},
                                 {'component':'updater','version':'1.1.0~20261114.1'}]
        legacy=SimpleNamespace(state=self.root,commands=native,transport=Transport())
        client=operations.AppClient(legacy);client.tick()
        self.assertEqual(client.executor.ledger['outbox'][0]['code'],'app_catalog_hash_mismatch')
        legacy.transport.fail=True
        with self.assertRaises(OSError):operations.AppClient(legacy).tick()
        self.assertEqual(len(operations.AppClient(legacy).executor.ledger['outbox']),1)

    def test_corrupt_installed_metadata_is_reported_without_blocking_other_jobs(self):
        self.run_job()
        root=self.root/'apps';folder=root/'noter';folder.mkdir(parents=True)
        (folder/'app.json').write_text('{broken')
        class Transport:
            body=None
            def poll_apps(inner,body):inner.body=body;return {'schema':1,'jobs':[]}
        self.native.installed=lambda:[{'component':'studentcore','version':'2.1.0~20261114.1'},
                                     {'component':'updater','version':'1.1.0~20261114.1'}]
        legacy=SimpleNamespace(state=self.root,commands=self.native,transport=Transport())
        self.assertTrue('manifest_root' in __import__('inspect').signature(operations.AppClient).parameters,'App inventory isolation is missing')
        operations.AppClient(legacy,manifest_root=root).tick()
        self.assertEqual(legacy.transport.body['issues'],[{'app_id':'noter','code':'installed_manifest_invalid'}])
        self.assertEqual(legacy.transport.body['installed'][0]['state'],'incomplete')

    def test_terminal_poll_replay_and_reject_cannot_turn_success_into_failure(self):
        self.run_job()
        class OfflineCatalog:
            def poll_apps(inner,body):return {'schema':1,'jobs':[dict(self.offer)]}
            def get(inner,path,limit):raise OSError('offline')
        self.native.installed=lambda:[{'component':'studentcore','version':'2.1.0~20261114.1'},
                                     {'component':'updater','version':'1.1.0~20261114.1'}]
        legacy=SimpleNamespace(state=self.root,commands=self.native,transport=OfflineCatalog())
        client=operations.AppClient(legacy);client.tick()
        self.assertEqual(client.executor.ledger['jobs']['a'*32]['phase'],'complete')
        self.assertEqual(client.executor.reject(self.offer,OSError('offline'))['state'],'succeeded')
        self.assertTrue(all(item['state']=='succeeded' for item in client.executor.ledger['outbox']))
        self.assertEqual(self.native.calls.count('install'),1)

    def test_crash_after_install_resumes_same_verified_job_across_other_catalog(self):
        original=self.native.activate_app
        self.native.activate_app=lambda *a,**kw:(_ for _ in ()).throw(SystemExit('power loss'))
        with self.assertRaises(SystemExit):self.run_job()
        self.assertEqual(self.runner.ledger['jobs']['a'*32]['phase'],'installed')
        newer=copy.deepcopy(self.catalog);newer.update(sequence=2,_manifest_sha256='d'*64)
        from app_catalog import accept_catalog
        self.runner.ledger['highwater']=accept_catalog(newer,self.runner.ledger['highwater']);self.runner.save()
        self.native.activate_app=original
        self.runner=operations.Executor(self.root,self.native)
        self.assertEqual(self.run_job()['state'],'succeeded')
        self.assertEqual(self.native.calls.count('install'),1)
        self.assertEqual(self.runner.ledger['highwater']['sequence'],2)

    def test_no_download_space_rejects_before_package_refresh_or_apply(self):
        with patch.object(operations.shutil,'disk_usage',return_value=SimpleNamespace(free=0)):
            self.assertEqual(self.run_job()['code'],'insufficient_download_space')
        self.assertNotIn('refresh',self.native.calls)
        self.assertNotIn('install',self.native.calls)

if __name__=='__main__':unittest.main()
