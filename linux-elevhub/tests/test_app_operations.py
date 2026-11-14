"""Durable app actions, loss/restart and exact restricted native removal."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'app'), str(ROOT/'linux-elevhub/administration')]
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
    def remove_app(self, descriptor): self.calls.append('remove');self.version=None;self.state='absent'
    def activate(self, core): self.calls.append('activate')


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

if __name__=='__main__':unittest.main()
