"""School intent is separate from observed app inventory and legacy offers."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from tests.test_app_catalog import envelope,descriptor
from app import app_catalog
from test_simple_updates import Fixture
from simple_updates import UpdateError
module=__import__('app_admin') if importlib.util.find_spec('app_admin') else None


class AppAdminTests(Fixture,unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(module,'School app administration is missing')
        self.initialize()
        self.crypto_app=patch.object(app_catalog.simple_release,'_minisign');self.crypto_app.start();self.addCleanup(self.crypto_app.stop)
        self.admin=module.AppAdmin(self.service,verifier=app_catalog.verify_catalog)
        self.service.app_admin=self.admin
        self.catalog_hash=self.publish()

    def publish(self,app=None,sequence=1):
        value=envelope(app)
        value['sequence']=sequence
        entry=value['apps'][0];data=b'synthetic public deb '+entry['descriptor']['id'].encode()
        entry['artifact'].update(size=len(data),sha256=hashlib.sha256(data).hexdigest())
        raw=json.dumps(value).encode();digest=hashlib.sha256(raw).hexdigest()
        folder=self.root/'public-apt-cache/manifests';folder.mkdir(exist_ok=True)
        (folder/(digest+'.json')).write_bytes(raw);(folder/(digest+'.minisig')).write_bytes(b'fake-publisher-signature')
        pool=self.root/'public-apt-cache/pool';pool.mkdir(exist_ok=True);(pool/entry['artifact']['name']).write_bytes(data)
        record={'manifest':'public-apt-cache/manifests/'+digest+'.json','signature':'public-apt-cache/manifests/'+digest+'.minisig',
                'role':'student','platform':'debian-amd64','component':'app-catalog'}
        self.config['catalog']=[r for r in self.config['catalog'] if r['component']!='app-catalog']+[record]
        return digest

    def legacy_report(self):
        self.service.poll('pc1',{'schema':1,'installed':[{'component':'studentcore','version':'2.1.0~20261114.1'},
            {'component':'updater','version':'1.1.0~20261114.1'}],'result':None})

    def poll(self,installed=None,result=None):
        return self.admin.poll('pc1',{'schema':1,'capabilities':['apps-v1'],'updater_version':'1.1.0~20261114.1',
            'core_version':'2.1.0~20261114.1','installed':installed or [],'issues':[],'result':result})

    def body(self,action='install'):
        return {'command_id':str(uuid.uuid4()),'expected_revision':self.admin.overview()['revision'],
            'devices':['pc1'],'app_id':'noter','action':action,'catalog_sha256':self.catalog_hash}

    def inventory(self,*,state='installed',version='1.0.0'):
        return [{'id':'noter','package':'makervaerkstedet-app-noter','version':version if state!='absent' else None,'state':state}]

    def test_unbridged_client_has_no_app_offers_and_legacy_response_stays_exact(self):
        with self.assertRaises(UpdateError):self.admin.order(self.body())
        response=self.service.poll('pc1',{'schema':1,'installed':[],'result':None})
        self.assertEqual(set(response),{'schema','revision','poll_after_seconds','desired'})
        self.assertEqual(response['desired'],[])
        self.assertFalse(self.admin.overview()['devices'][0]['capable'])

    def test_install_order_is_idempotent_and_success_requires_actual_inventory(self):
        self.legacy_report();self.poll();body=self.body()
        receipt=self.admin.order(body);self.assertEqual(self.admin.order(copy.deepcopy(body)),receipt)
        jobs=self.poll()['jobs'];self.assertEqual(len(jobs),1);job=jobs[0]
        self.assertEqual((job['action'],job['app_id'],job['catalog_sha256']),('install','noter',self.catalog_hash))
        self.assertEqual(self.admin.overview()['jobs'][0]['state'],'queued')
        self.poll(result={'job_id':job['job_id'],'revision':job['revision'],'state':'succeeded','code':None})
        failed=self.admin.overview()['jobs'][0]
        self.assertEqual((failed['state'],failed['code']),('failed','app_observed_state_mismatch'))

    def test_installed_and_removed_states_are_observed_and_old_job_is_superseded(self):
        self.legacy_report();self.poll();self.admin.order(self.body());install=self.poll()['jobs'][0]
        self.poll(self.inventory(),{'job_id':install['job_id'],'revision':install['revision'],'state':'succeeded','code':None})
        self.assertEqual(self.admin.overview()['jobs'][0]['state'],'succeeded')
        self.admin.order(self.body('remove'));remove=self.poll(self.inventory())['jobs'][0]
        self.poll(self.inventory(state='absent'),{'job_id':remove['job_id'],'revision':remove['revision'],'state':'succeeded','code':None})
        self.assertEqual(self.admin.overview()['devices'][0]['installed'][0]['state'],'absent')
        self.assertFalse(self.poll()['jobs'])

    def test_stale_revision_unknown_devices_and_package_input_are_rejected(self):
        self.legacy_report();self.poll();body=self.body();body['expected_revision']+=1
        with self.assertRaises(UpdateError):self.admin.order(body)
        body=self.body();body['devices']=['not-enrolled']
        with self.assertRaises(UpdateError):self.admin.order(body)
        body=self.body();body['package']='curl'
        with self.assertRaises(UpdateError):self.admin.order(body)

    def test_new_intent_supersedes_queued_action_for_same_app_only(self):
        self.legacy_report();self.poll();self.admin.order(self.body());self.admin.order(self.body('remove'))
        overview=self.admin.overview()
        self.assertEqual(sorted(j['state'] for j in overview['jobs']),['queued','superseded'])
        self.assertEqual([j['action'] for j in self.poll()['jobs']],['remove'])

    def test_defaults_do_not_silently_change_existing_devices(self):
        overview=self.admin.overview()
        receipt=self.admin.defaults({'command_id':str(uuid.uuid4()),'expected_revision':overview['revision'],
            'catalog_sha256':self.catalog_hash,'apps':['noter']})
        self.assertEqual(receipt['defaults'],['noter'])
        self.legacy_report();self.assertFalse(self.poll()['jobs'])

    def test_defaults_apply_once_to_a_new_enrolled_device_after_bridge_report(self):
        state=self.admin.overview()
        self.admin.defaults({'command_id':str(uuid.uuid4()),'expected_revision':state['revision'],
            'catalog_sha256':self.catalog_hash,'apps':['noter']})
        register=self.root/'registry.json';value=json.loads(register.read_bytes())
        value['enheder']['pc2']={'token_sha256':hashlib.sha256(b'v'*43).hexdigest(),
                               'ui_token_sha256':hashlib.sha256(b'w'*43).hexdigest()}
        register.write_text(json.dumps(value))
        self.config['device_bindings']['pc2']={'role':'student','platform':'debian-amd64'}
        body={'schema':1,'capabilities':['apps-v1'],'updater_version':'1.1.0~20261114.1',
              'core_version':'2.1.0~20261114.1','installed':[],'issues':[],'result':None}
        self.assertEqual(self.admin.poll('pc2',body)['jobs'],[])
        self.service.poll('pc2',{'schema':1,'installed':[{'component':'studentcore','version':body['core_version']},
            {'component':'updater','version':body['updater_version']}],'result':None})
        job=self.admin.poll('pc2',body)['jobs'][0]
        self.assertEqual((job['action'],job['app_id']),('install','noter'))
        installed=body|{'installed':self.inventory(),'result':{'job_id':job['job_id'],'revision':job['revision'],'state':'succeeded','code':None}}
        self.assertEqual(self.admin.poll('pc2',installed)['jobs'],[])
        # An explicit later removal is never undone by the school defaults.
        self.assertEqual(self.admin.poll('pc2',body|{'installed':self.inventory(state='absent')})['jobs'],[])

    def test_fake_capability_without_matching_legacy_updater_report_is_not_enough(self):
        self.poll()
        with self.assertRaises(UpdateError):self.admin.order(self.body())

if __name__=='__main__':unittest.main()
