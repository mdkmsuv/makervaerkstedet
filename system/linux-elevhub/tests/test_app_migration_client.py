"""Real publisher signatures around a synthetic, explicitly isolated apt adapter."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_apt_client as legacy
M=legacy.M

LEGACY='2.0.0~20261114.2'
MODULAR='2.1.0~20261114.1'
BRIDGE='1.1.0~20261114.1'
FOUR={'laser','folie','3dprint','microbit'}


class Native(legacy.IsolatedCommands):
    def __init__(self):
        super().__init__();self.versions={'studentcore':LEGACY,'updater':BRIDGE}
        self.apps={};self.manifests={};self.drop=set();self.fail_download=None

    def app_inventory(self,descriptors):
        return [{'id':a['id'],'package':a['package'],'version':self.apps.get(a['id']),
                 'state':'installed' if a['id'] in self.apps else 'absent'} for a in descriptors]

    def app_matches(self,app):return self.manifests.get(app['id'])==app
    def preflight_app(self,app,**_):return []

    def download(self,artifact,directory):
        if artifact['package']==self.fail_download:raise M.UpdateError('artifact_hash_mismatch')
        return super().download(artifact,directory)

    def install_migration(self,files,descriptors,targets):
        self.installs+=1;self.events.append(('migration-install',len(files),deepcopy(targets)))
        for file in files:
            artifact=self.downloaded[str(file)]
            for component,(package,_) in M.DEBIAN_COMPONENTS.items():
                if artifact['package']==package:self.versions[component]=artifact['version']
            for app in descriptors:
                if artifact['package']==app['package'] and app['id'] not in self.drop:
                    self.apps[app['id']]=app['version'];self.manifests[app['id']]=deepcopy(app)
        if self.crash:raise SystemExit('synthetic interruption after dpkg')

    def activate_migration(self,core,descriptors):
        self.activations+=1;self.events.append(('activate-migration',len(descriptors)))


class MigrationClientTests(unittest.TestCase):
    signed=legacy.AptClient.signed

    def setUp(self):
        legacy.AptClient.setUp(self)
        self.commands=Native()
        get=self.transport.get
        def available(path,limit):
            if path not in self.transport.files:raise OSError('metadata temporarily unavailable')
            return get(path,limit)
        self.transport.get=available
        self.client=M.Client(self.state,self.transport,self.commands,minisign=self.tool,public_key=self.key)
        self.offer=self.signed(version=MODULAR,sequence=8,revision=100)
        self.transport.response['desired']=[self.offer]
        self.catalog,self.catalog_hash=self.make_catalog()
        self.bind(scratch=False)

    def make_catalog(self,sequence=11):
        apps=[]
        for app_id in sorted(FOUR|{'scratch'}):
            descriptor=json.loads((legacy.ROOT/'apps'/app_id/'app.json').read_text(encoding='utf-8'))
            data=b'synthetic package '+descriptor['package'].encode()
            name=f"{descriptor['package']}_{descriptor['version']}_{descriptor['architecture']}.deb"
            apps.append({'descriptor':descriptor,'sequence':1,'source_commit':'a'*40,
                         'artifact':{'name':name,'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),
                                     'url':f'https://github.com/{M.REPOSITORY}/releases/download/migration-test/{name}'}})
        value={'schema':2,'kind':'makervaerkstedet-app-catalog','repository':M.REPOSITORY,
               'role':'student','platform':'debian-amd64','component':'app-catalog',
               'version':'2026.11.14.1','sequence':sequence,'apps':apps}
        raw=json.dumps(value).encode();digest=hashlib.sha256(raw).hexdigest()
        message=self.root/(digest+'.json');message.write_bytes(raw);signature=message.with_suffix('.minisig')
        subprocess.run([str(self.tool),'-S','-s',str(self.secret),'-m',str(message),'-x',str(signature)],
                       check=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.transport.files['/apt/apps/catalog/'+digest+'.json']=raw
        self.transport.files['/apt/apps/catalog/'+digest+'.minisig']=signature.read_bytes()
        return value,digest

    def bind(self,*,scratch):
        selected=sorted(FOUR|({'scratch'} if scratch else set()))
        self.binding={'schema':1,'core_manifest_sha256':self.offer['manifest_sha256'],'revision':100,
                      'catalog_sha256':self.catalog_hash,'apps':selected,'completed':False}
        self.binding_path='/api/device/migration/'+self.offer['manifest_sha256']+'/100'
        self.transport.files[self.binding_path]=json.dumps(self.binding).encode()

    def test_preserves_four_in_one_transaction_and_does_not_add_absent_scratch(self):
        self.client.tick()
        self.assertEqual(set(self.commands.apps),FOUR)
        self.assertEqual(self.commands.installs,1)
        self.assertIn(('activate-migration',4),self.commands.events)
        event=next(e for e in self.commands.events if e[0]=='migration-install')
        self.assertEqual(event[1],5)
        self.assertEqual(self.client.ledger['jobs']['studentcore:100']['phase'],'complete')
        self.assertEqual(self.client.ledger['outbox'][-1]['state'],'succeeded')

    def test_scratch_is_preserved_only_when_actually_installed(self):
        self.commands.versions['scratch']='15.1.1+local.20261104.1';self.bind(scratch=True)
        self.client.tick()
        self.assertEqual(set(self.commands.apps),FOUR|{'scratch'})
        self.assertEqual(next(e for e in self.commands.events if e[0]=='migration-install')[1],6)

    def test_bad_signature_and_last_archive_failure_leave_legacy_core_untouched(self):
        self.transport.files['/apt/apps/catalog/'+self.catalog_hash+'.minisig']=b'invalid'
        self.client.tick()
        self.assertEqual(self.commands.installs,0)
        self.assertEqual(self.commands.versions['studentcore'],LEGACY)
        self.catalog,self.catalog_hash=self.make_catalog(12);self.bind(scratch=False)
        self.commands.fail_download='makervaerkstedet-app-microbit'
        self.client.tick()
        self.assertEqual(self.commands.installs,0)
        self.assertEqual(self.commands.versions['studentcore'],LEGACY)

    def test_binding_cannot_silently_add_scratch_or_change_core_intent(self):
        for change in ({'apps':sorted(FOUR|{'scratch'})},{'core_manifest_sha256':'b'*64},{'revision':101},
                       {'completed':True},{'completed':1}):
            binding={**self.binding,**change};self.transport.files[self.binding_path]=json.dumps(binding).encode()
            self.client.tick()
            self.assertEqual(self.commands.installs,0)

    def test_interrupted_native_transaction_repairs_missing_app_even_when_core_matches(self):
        self.commands.drop={'laser'};self.commands.crash=True
        with self.assertRaises(SystemExit):self.client.tick()
        self.assertEqual(self.commands.versions['studentcore'],MODULAR)
        self.assertNotIn('laser',self.commands.apps)
        # Verified metadata is durable; unrelated server catalog changes cannot
        # make an already-authorized interrupted migration lose its exact input.
        del self.transport.files['/apt/apps/catalog/'+self.catalog_hash+'.json']
        del self.transport.files['/apt/apps/catalog/'+self.catalog_hash+'.minisig']
        del self.transport.files[self.binding_path]
        self.commands.drop=set();self.commands.crash=False
        self.client=M.Client(self.state,self.transport,self.commands,minisign=self.tool,public_key=self.key)
        self.client.tick()
        self.assertEqual(set(self.commands.apps),FOUR)
        self.assertEqual(self.commands.installs,2)
        self.assertEqual(self.client.ledger['outbox'][-1]['state'],'succeeded')

    def test_core_version_alone_never_produces_success_for_a_missing_app(self):
        self.commands.drop={'microbit'}
        self.client.tick()
        self.assertEqual(self.commands.versions['studentcore'],MODULAR)
        self.assertNotEqual(self.client.ledger['jobs']['studentcore:100']['phase'],'complete')
        self.assertEqual(self.client.ledger['outbox'][-1]['state'],'failed')

    def test_complete_acknowledged_migration_does_not_restore_later_removed_app(self):
        self.client.tick();self.assertEqual(set(self.commands.apps),FOUR)
        self.binding['completed']=True;self.transport.files[self.binding_path]=json.dumps(self.binding).encode()
        self.transport.response['desired']=[];self.client.tick()
        self.commands.apps.pop('laser');self.commands.manifests.pop('laser')
        # Even a replay of the old already-completed core intent must not undo
        # a later separately authorized app removal.
        self.transport.response['desired']=[self.offer]
        self.client.tick()
        self.assertNotIn('laser',self.commands.apps)
        self.assertEqual(self.commands.installs,1)

    def test_missing_bridge_and_older_legacy_baseline_are_rejected_locally(self):
        for values in ({'studentcore':LEGACY,'updater':'1.0.0'},
                       {'studentcore':'2.0.0~20261114.1','updater':BRIDGE}):
            self.commands.versions=values;self.client.tick()
            self.assertEqual(self.commands.installs,0)

    def test_updater_bridge_cannot_implicitly_pull_split_core_on_an_older_baseline(self):
        self.transport.response['desired']=[self.signed('updater',version=BRIDGE,revision=101)]
        self.commands.versions={'studentcore':'2.0.0~20261114.1','updater':'1.0.0'}
        self.client.tick();self.assertEqual(self.commands.installs,0)
        self.assertEqual(self.client.ledger['outbox'][-1]['code'],'legacy_core_baseline_required')

    def test_missing_offer_does_not_confirm_migration_or_allow_app_jobs(self):
        self.client.tick();self.transport.response['desired']=[];self.client.tick()
        self.assertEqual(self.client.ledger['jobs']['studentcore:100']['migration']['phase'],'reported')
        self.assertTrue(self.client.migration_pending)
        from app_operations import AppClient
        self.transport.poll_apps=lambda _: {'schema':1,'jobs':[{'job_id':'e'*32,'revision':200,
            'action':'remove','app_id':'laser','catalog_sha256':self.catalog_hash}]}
        result=AppClient(self.client,manifest_root=self.root/'empty-manifests').tick()
        self.assertEqual(result['state'],'migration_pending')
        self.assertEqual(self.commands.installs,1)

    def test_completed_binding_releases_apps_after_status_loss_and_a_later_core_update(self):
        self.client.tick();get=self.transport.get
        self.binding['completed']=True;self.transport.files[self.binding_path]=json.dumps(self.binding).encode()
        def unavailable(path,limit):
            if path==self.binding_path:raise OSError('temporary status outage')
            return get(path,limit)
        self.transport.get=unavailable
        self.transport.response['desired']=[self.signed(version='2.1.1',sequence=9,revision=101)]
        self.client.tick()
        self.assertEqual(self.commands.versions['studentcore'],'2.1.1')
        self.assertTrue(self.client.migration_pending)
        # The completed server binding remains authoritative after later
        # independently authorized changes to either the core or app set.
        self.commands.apps.pop('laser');self.commands.manifests.pop('laser')
        self.transport.get=get;self.transport.response['desired']=[];self.client.tick()
        self.assertFalse(self.client.migration_pending)
        self.assertEqual(self.client.ledger['jobs']['studentcore:100']['migration']['phase'],'acknowledged')
        self.assertNotIn('laser',self.commands.apps)

    def test_reused_completed_revision_reports_failure_without_aborting_poll(self):
        self.client.tick();self.binding['completed']=True
        self.transport.files[self.binding_path]=json.dumps(self.binding).encode()
        changed=deepcopy(self.offer);changed['manifest_sha256']='f'*64
        changed['manifest_path']='/apt/manifests/'+'f'*64+'.json'
        changed['signature_path']='/apt/manifests/'+'f'*64+'.minisig'
        self.transport.response['desired']=[changed];self.client.tick()
        self.assertEqual(self.client.ledger['outbox'][-1]['code'],'revision_manifest_changed')
        self.assertEqual(self.commands.installs,1)

    def test_crash_between_completion_and_outbox_write_resends_success(self):
        self.client.tick();self.client.ledger['outbox']=[];self.client.save()
        self.client=M.Client(self.state,self.transport,self.commands,minisign=self.tool,public_key=self.key)
        self.client.tick()
        self.assertEqual(self.commands.installs,1)
        self.assertEqual(self.client.ledger['outbox'][-1]['state'],'succeeded')

    def test_durable_resume_survives_unrelated_higher_catalog_without_lowering_highwater(self):
        self.commands.drop={'laser'};self.commands.crash=True
        with self.assertRaises(SystemExit):self.client.tick()
        from app_operations import Executor,load_verified_catalog
        from app_catalog import accept_catalog
        _,new_hash=self.make_catalog(12)
        executor=Executor(self.state,self.commands)
        executor.ledger['highwater']=accept_catalog(load_verified_catalog(self.client,new_hash),executor.ledger['highwater'])
        executor.save()
        self.commands.drop=set();self.commands.crash=False
        self.client=M.Client(self.state,self.transport,self.commands,minisign=self.tool,public_key=self.key)
        self.client.tick()
        self.assertEqual(set(self.commands.apps),FOUR)
        self.assertEqual(Executor(self.state,self.commands).ledger['highwater']['sequence'],12)

    def test_no_app_downgrade_and_no_changed_journal_identity(self):
        self.commands.apps['laser']='9.0.0';self.client.tick()
        self.assertEqual(self.commands.installs,0)
        self.assertEqual(self.client.ledger['outbox'][-1]['code'],'app_downgrade_rejected')
        self.commands.apps={};self.commands.crash=True
        with self.assertRaises(SystemExit):self.client.tick()
        job=self.client.ledger['jobs']['studentcore:100']
        job['migration']['entries'][0]['descriptor']['description']='changed';self.client.save()
        self.commands.crash=False
        self.client=M.Client(self.state,self.transport,self.commands,minisign=self.tool,public_key=self.key)
        self.client.tick();self.assertEqual(self.commands.installs,1)
        self.assertEqual(self.client.ledger['outbox'][-1]['code'],'migration_catalog_identity_changed')

    def test_native_apt_plan_blocks_removal_and_unrelated_school_mutations_before_apply(self):
        targets={'makervaerkstedet-elev':MODULAR,'makervaerkstedet-app-laser':'1.0.0'}
        for simulation in ('Remv makervaerkstedet-app-folie [1.0.0]',
                           'Inst makervaerkstedet-app-foreign (1.0.0 local [all])',
                           'Inst makervaerkstedet-app-laser (0.9.0 local [all])'):
            commands=M.Commands(options=[]);calls=[]
            def call(argv,**_):calls.append(argv);return simulation
            commands.call=call
            with self.assertRaisesRegex(ValueError,'unsafe_migration_install_plan'):
                commands.install([self.root/'candidate.deb'],False,migration_targets=targets)
            self.assertTrue(all('--simulate' in argv for argv in calls))
        from app_migration import install_plan
        self.assertTrue(install_plan('Inst python3-pil (11.1.0 Debian [amd64])\n'
            'Inst makervaerkstedet-elev ['+LEGACY+'] ('+MODULAR+' local [all])',targets))
        self.assertTrue(install_plan('Inst makervaerkstedet-elev ['+LEGACY+'] ('+MODULAR+' local [all]) []\n'
                                    'Conf makervaerkstedet-app-laser (1.0.0 local [all]) [python3 on makervaerkstedet-elev]',targets))

    def test_expanded_space_is_aggregated_for_apps_on_the_same_volume(self):
        commands=M.Commands(app_root=self.root)
        archive={'allocation':{'app':{'bytes':60*1024**2,'files':1,'directories':1}}}
        with patch.object(M.shutil,'disk_usage',return_value=SimpleNamespace(free=260*1024**2)):
            commands.check_app_space([archive])
            with self.assertRaisesRegex(ValueError,'insufficient_install_space'):
                commands.check_app_space([archive,archive])

    def test_native_authentication_is_only_added_to_the_fixed_migration_route(self):
        native=M.HTTPS.__new__(M.HTTPS);native.origin='https://127.0.0.1';native.computer='test-device'
        native.token='fixture-native-token';sent=[]
        class Response:
            status=200;headers={'Content-Length':'2'}
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def read(self,_):return b'{}'
        class Opener:
            def open(self,request,**_):sent.append(request);return Response()
        native.opener=Opener()
        native.get(self.binding_path,16384)
        self.assertEqual(sent[-1].get_header('Authorization'),'Bearer fixture-native-token')
        native.get(self.binding_path+'?another=path',16384)
        self.assertIsNone(sent[-1].get_header('Authorization'))


if __name__=='__main__':unittest.main()
