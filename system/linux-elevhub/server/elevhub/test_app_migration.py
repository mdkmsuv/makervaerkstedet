"""Migration authorization is durable; an ACK alone never proves app preservation."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

ROOT=Path(__file__).resolve().parents[4]
sys.path[:0]=[str(ROOT),str(ROOT/'system'),str(Path(__file__).parent)]

from app import app_catalog
from app_admin import AppAdmin
from simple_updates import UpdateError, revoke_device_updates
from test_simple_updates import Fixture
from tests.test_app_catalog import envelope

BASE='2.0.0~20261114.2'
CORE='2.1.0~20261114.1'
BRIDGE='1.1.0~20261114.1'
BUILTINS={'laser','folie','3dprint','microbit','scratch'}
PRESERVED=sorted(BUILTINS-{'scratch'})


def publish_catalog(fixture,sequence=1,omit=(),future=None):
    entries=[]
    for app_id in sorted(BUILTINS-set(omit)):
        descriptor=json.loads((ROOT/'apps'/app_id/'app.json').read_text(encoding='utf-8'))
        if app_id==future:descriptor['min_core']='9.0.0'
        entry=envelope(descriptor)['apps'][0];entry['sequence']=sequence
        data=b'synthetic preserved package '+app_id.encode()
        entry['artifact'].update(size=len(data),sha256=hashlib.sha256(data).hexdigest())
        pool=fixture.root/'public-apt-cache/pool';pool.mkdir(exist_ok=True)
        (pool/entry['artifact']['name']).write_bytes(data)
        entries.append(entry)
    value=envelope();value.update(sequence=sequence,apps=entries)
    raw=json.dumps(value).encode();digest=hashlib.sha256(raw).hexdigest()
    folder=fixture.root/'public-apt-cache/manifests';folder.mkdir(exist_ok=True)
    (folder/(digest+'.json')).write_bytes(raw);(folder/(digest+'.minisig')).write_bytes(b'synthetic signature')
    fixture.config['catalog']=[r for r in fixture.config['catalog'] if r['component']!='app-catalog']+[
        {'manifest':'public-apt-cache/manifests/'+digest+'.json','signature':'public-apt-cache/manifests/'+digest+'.minisig',
         'role':'student','platform':'debian-amd64','component':'app-catalog'}]
    fixture.entries={entry['descriptor']['id']:entry['descriptor'] for entry in entries}
    return digest


def native_report(core=BASE,updater=BRIDGE,scratch=False,result=None):
    installed=[{'component':'studentcore','version':core}]
    if updater is not None:installed.append({'component':'updater','version':updater})
    if scratch:installed.append({'component':'scratch','version':'15.1.1+local.20261104.1'})
    return {'schema':1,'installed':installed,'result':result}


class MigrationTests(Fixture,unittest.TestCase):
    def setUp(self):
        self.initialize()
        self.crypto_app=patch.object(app_catalog.simple_release,'_minisign')
        self.crypto_app.start();self.addCleanup(self.crypto_app.stop)
        self.attach()
        self.catalog_hash=publish_catalog(self)
        self.target=self.release(version=CORE,sequence=3)

    def attach(self):
        self.admin=AppAdmin(self.service,verifier=app_catalog.verify_catalog)
        self.service.app_admin=self.admin

    def core_report(self,**kwargs):return self.service.poll('pc1',native_report(**kwargs))

    def app_report(self,ids=PRESERVED,core=CORE,*,result=None,issues=None):
        body={'schema':1,'capabilities':['apps-v1'],'updater_version':BRIDGE,'core_version':core,
              'installed':[{'id':name,'package':self.entries[name]['package'],'version':self.entries[name]['version'],'state':'installed'} for name in ids],
              'issues':issues or [],'result':result}
        return self.admin.poll('pc1',body)

    def authorize(self,scratch=False):
        self.core_report(scratch=scratch)
        body=self.order(self.target,revision=self.service.overview()['revision'])
        return body,self.service.order(body)

    def wire(self,receipt):return self.service.migration('pc1',self.target,receipt['revision'])

    def cancellation(self,receipt):
        return {'command_id':str(uuid.uuid4()),'expected_revision':self.service.overview()['revision'],
                'device':'pc1','core_manifest_sha256':self.target,'migration_revision':receipt['revision']}

    def test_failed_migration_can_stop_retries_without_rebinding_or_losing_history(self):
        _,original=self.authorize()
        self.core_report(result={'revision':original['revision'],'state':'failed','code':'insufficient_migration_space'})
        command=self.cancellation(original)
        receipt=self.service.cancel_migration(command)
        self.assertEqual(self.service.cancel_migration(command),receipt)
        device=next(d for d in self.service.overview()['devices'] if d['device']=='pc1')
        self.assertIsNone(device['migration']);self.assertEqual(device['desired'],[])
        self.assertEqual(device['result']['code'],'insufficient_migration_space')
        with self.assertRaisesRegex(UpdateError,'migration_not_found'):self.wire(original)
        _,new=self.authorize()
        self.assertGreater(new['revision'],original['revision'])
        self.assertEqual(self.wire(new)['apps'],PRESERVED)

    def test_cancellation_must_match_revision_identity_and_an_unfinished_migration(self):
        _,receipt=self.authorize();command=self.cancellation(receipt)
        for changed in ({'expected_revision':command['expected_revision']-1},
                        {'core_manifest_sha256':'f'*64},{'migration_revision':receipt['revision']+1}):
            with self.subTest(changed=changed),self.assertRaises(UpdateError):
                self.service.cancel_migration(command|changed)
            self.assertFalse(self.wire(receipt)['completed'])
        self.core_report(core=CORE,result={'revision':receipt['revision'],'state':'succeeded','code':None})
        self.app_report()
        with self.assertRaisesRegex(UpdateError,'migration_not_pending'):self.service.cancel_migration(self.cancellation(receipt))
        self.assertTrue(self.wire(receipt)['completed'])

    def test_unavailable_identity_never_revokes_a_valid_migration_binding(self):
        _,receipt=self.authorize()
        with patch.object(self.service.registry,'identitet',return_value=None):
            with self.assertRaisesRegex(UpdateError,'migration_state_unavailable') as caught:self.wire(receipt)
            self.assertEqual(caught.exception.status,503)

    def active(self):
        return [item for item in self.service.overview()['devices'][0]['desired'] if item['component']=='studentcore']

    def app_order(self):
        return {'command_id':str(uuid.uuid4()),'expected_revision':self.admin.overview()['revision'],
                'devices':['pc1'],'app_id':'laser','action':'remove','catalog_sha256':self.catalog_hash}

    def test_modular_core_and_bridge_updater_cannot_skip_observed_prerequisites(self):
        with self.assertRaisesRegex(UpdateError,'updater_bridge_required'):
            self.service.order(self.order(self.target,revision=self.service.overview()['revision']))
        bridge=self.release(version=BRIDGE,sequence=4,component='updater')
        self.core_report(core='2.0.0~20261114.1',updater='1.0.0')
        with self.assertRaisesRegex(UpdateError,'legacy_core_baseline_required'):
            self.service.order(self.order(bridge,component='updater',revision=self.service.overview()['revision']))
        self.core_report(core=BASE,updater='1.0.0')
        self.service.order(self.order(bridge,component='updater',revision=self.service.overview()['revision']))
        self.assertEqual(self.core_report(core='2.0.0~20261114.1',updater='1.0.0')['desired'],[])
        self.core_report(core='2.0.0~20261114.1')
        with self.assertRaisesRegex(UpdateError,'legacy_core_baseline_required'):
            self.service.order(self.order(self.target,revision=self.service.overview()['revision']))
        self.core_report()
        receipt=self.service.order(self.order(self.target,revision=self.service.overview()['revision']))
        self.assertIn('studentcore',{item['component'] for item in self.core_report()['desired']})
        self.assertNotIn('studentcore',{item['component'] for item in self.core_report(updater='1.0.0')['desired']})
        self.assertIs(self.wire(receipt)['completed'],False)

    def test_binding_is_exact_idempotent_and_only_actual_legacy_scratch_is_preserved(self):
        overview=self.admin.overview()
        self.admin.defaults({'command_id':str(uuid.uuid4()),'expected_revision':overview['revision'],
            'catalog_sha256':self.catalog_hash,'apps':['scratch']})
        body,receipt=self.authorize()
        expected={'schema':1,'core_manifest_sha256':self.target,'revision':receipt['revision'],
                  'catalog_sha256':self.catalog_hash,'apps':PRESERVED,'completed':False}
        self.assertEqual(self.wire(receipt),expected)
        self.assertEqual(self.service.order(copy.deepcopy(body)),receipt)
        self.service=self.reopen();self.attach()
        self.assertEqual(self.wire(receipt),expected)
        self.assertEqual(self.service.order(copy.deepcopy(body)),receipt)
        # A different device's latest native report independently preserves Scratch.
        registry=json.loads((self.root/'registry.json').read_text(encoding='utf-8'))
        registry['enheder']['pc2']={'token_sha256':'e'*64,'ui_token_sha256':'f'*64}
        (self.root/'registry.json').write_text(json.dumps(registry),encoding='utf-8')
        self.config['device_bindings']['pc2']={'role':'student','platform':'debian-amd64'}
        self.service.poll('pc2',native_report(scratch=True))
        body=self.order(self.target,devices=['pc2'],revision=self.service.overview()['revision'])
        second=self.service.order(body)
        self.assertEqual(self.service.migration('pc2',self.target,second['revision'])['apps'],sorted(BUILTINS))

    def test_all_five_catalog_entries_and_compatibility_are_required_before_order(self):
        self.core_report()
        self.config['catalog']=[r for r in self.config['catalog'] if r['component']!='app-catalog']
        with self.assertRaisesRegex(UpdateError,'migration_catalog_required'):
            self.service.order(self.order(self.target,revision=self.service.overview()['revision']))
        publish_catalog(self,sequence=2,omit=('scratch',))
        with self.assertRaisesRegex(UpdateError,'migration_catalog_incomplete'):
            self.service.order(self.order(self.target,revision=self.service.overview()['revision']))
        publish_catalog(self,sequence=3,future='scratch')
        with self.assertRaisesRegex(UpdateError,'migration_catalog_incompatible'):
            self.service.order(self.order(self.target,revision=self.service.overview()['revision']))
        self.assertFalse(self.active())

    def test_catalog_change_never_rebinds_an_authorized_migration(self):
        _,receipt=self.authorize();before=self.wire(receipt)
        latest=publish_catalog(self,sequence=2)
        self.assertNotEqual(latest,self.catalog_hash)
        self.admin.overview()
        self.assertEqual(self.wire(receipt),before)
        with self.assertRaisesRegex(UpdateError,'migration_in_progress'):
            self.service.order(self.order(self.target,revision=self.service.overview()['revision']))
        self.service=self.reopen();self.attach();self.assertEqual(self.wire(receipt),before)

    def test_lazy_app_initialization_uses_the_existing_transaction_without_lock_reentry(self):
        del self.service.app_admin
        with self.service.transaction() as db:
            for name in ('apps_meta','apps_catalogs','apps_reports','apps_enrolled','apps_jobs'):
                db.execute('DROP TABLE '+name)
        # Same strict parser; only the fixture's existing crypto replacement is shared.
        with patch('app_admin.contract',return_value=app_catalog):
            self.core_report()
            revision=self.service.overview()['revision']
            receipt=self.service.order(self.order(self.target,revision=revision))
        self.assertEqual(receipt['revision'],revision+1)
        self.assertIs(self.wire(receipt)['completed'],False)

    def test_repeated_core_overviews_reuse_real_artifact_hashes_and_recheck_changes(self):
        del self.service.app_admin
        with patch('app_admin.contract',return_value=app_catalog), patch('app_admin.hashlib.file_digest',wraps=hashlib.file_digest) as digest:
            first=self.service.overview()
            self.assertEqual(digest.call_count,5)
            for _ in range(3):self.assertEqual(self.service.overview()['revision'],first['revision'])
            self.assertEqual(digest.call_count,5,'An unchanged Scratch archive must not be rehashed on every core overview')
            path=next((self.root/'public-apt-cache/pool').iterdir())
            path.write_bytes(path.read_bytes())
            self.service.overview()
            self.assertEqual(digest.call_count,6,'Changed artifact stamps must still force a real digest check')

    def test_private_app_cache_recovers_schema_after_rollback_and_prefers_live_admin(self):
        del self.service.app_admin
        with self.service.transaction() as db:
            for name in ('apps_meta','apps_catalogs','apps_reports','apps_enrolled','apps_jobs'):
                db.execute('DROP TABLE '+name)
        with patch('app_admin.contract',return_value=app_catalog):
            with self.assertRaisesRegex(RuntimeError,'synthetic rollback'):
                with self.service.transaction() as db:
                    self.service._apps(db).catalog(db)
                    raise RuntimeError('synthetic rollback')
            with self.service.transaction() as db:
                self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='apps_meta'").fetchone())
            self.service.overview()
            self.core_report()
            receipt=self.service.order(self.order(self.target,revision=self.service.overview()['revision']))
        self.assertIs(self.wire(receipt)['completed'],False)
        self.service.app_admin=self.admin
        with self.service.transaction() as db:self.assertIs(self.service._apps(db),self.admin)

    def test_shared_revision_catalog_acceptance_is_atomic_and_not_overwritten(self):
        self.core_report();revision=self.service.overview()['revision']
        publish_catalog(self,sequence=2)
        with self.assertRaisesRegex(UpdateError,'revision_conflict'):
            self.service.order(self.order(self.target,revision=revision))
        next_revision=self.service.overview()['revision']
        self.assertEqual(next_revision,revision+1)
        receipt=self.service.order(self.order(self.target,revision=next_revision))
        self.assertEqual(receipt['revision'],next_revision+1)
        self.assertEqual(self.wire(receipt)['revision'],receipt['revision'])

    def test_core_ack_survives_restart_and_null_result_until_app_inventory_arrives(self):
        _,receipt=self.authorize();revision=receipt['revision']
        ack={'revision':revision,'state':'succeeded','code':None}
        self.core_report(core=CORE,result=ack)
        self.assertTrue(self.active());self.assertIs(self.wire(receipt)['completed'],False)
        self.service=self.reopen();self.attach();self.core_report(core=CORE)
        self.app_report(ids=PRESERVED[:-1]);self.assertTrue(self.active())
        self.app_report(issues=[{'app_id':'laser','code':'runtime_not_ready'}]);self.assertTrue(self.active())
        self.app_report();self.assertFalse(self.active())
        self.assertIs(self.wire(receipt)['completed'],True)
        self.admin.order(self.app_order())
        self.core_report(core=CORE);self.app_report(ids=[])
        self.assertFalse(self.active(),'A later authorized removal must not restart a completed migration')

    def test_matching_inventory_without_ack_or_wrong_core_never_completes(self):
        _,receipt=self.authorize()
        self.core_report(core=CORE);self.app_report();self.assertTrue(self.active())
        self.core_report(core=BASE,result={'revision':receipt['revision'],'state':'succeeded','code':None})
        self.assertTrue(self.active())
        self.core_report(core=CORE)
        self.app_report();self.assertFalse(self.active())

    def test_artifact_cache_is_required_before_authorization_not_after_observed_install(self):
        self.core_report();self.admin.overview()
        metadata=json.loads((self.root/'public-apt-cache/manifests'/(self.catalog_hash+'.json')).read_text(encoding='utf-8'))
        scratch=next(entry for entry in metadata['apps'] if entry['descriptor']['id']=='scratch')
        artifact=self.root/'public-apt-cache/pool'/scratch['artifact']['name']
        data=artifact.read_bytes();artifact.unlink()
        with self.assertRaisesRegex(UpdateError,'app_artifact_unavailable'):
            self.service.order(self.order(self.target,revision=self.service.overview()['revision']))
        artifact.write_bytes(data)
        _,receipt=self.authorize()
        artifact.unlink()  # Scratch was not selected and every preserved app is observed installed.
        self.core_report(core=CORE,result={'revision':receipt['revision'],'state':'succeeded','code':None})
        self.app_report()
        self.assertIs(self.wire(receipt)['completed'],True)

    def test_missing_signed_metadata_keeps_completion_false_until_retry_inventory(self):
        _,receipt=self.authorize()
        self.core_report(core=CORE,result={'revision':receipt['revision'],'state':'succeeded','code':None})
        metadata=self.root/'public-apt-cache/manifests'/(self.catalog_hash+'.json')
        original=metadata.read_bytes();metadata.unlink()
        self.app_report();self.assertIs(self.wire(receipt)['completed'],False)
        self.assertTrue(self.active())
        metadata.write_bytes(original)
        self.app_report();self.assertIs(self.wire(receipt)['completed'],True)
        self.assertFalse(self.active())
        metadata.unlink()
        self.service=self.reopen();self.attach()
        self.assertIs(self.wire(receipt)['completed'],True,'Confirmed completion is monotonic after catalog cleanup')

    def test_app_order_and_existing_execution_are_held_until_migration_completes(self):
        _,receipt=self.authorize()
        with self.assertRaisesRegex(UpdateError,'migration_in_progress'):self.admin.order(self.app_order())
        with self.service.transaction() as db:
            self.admin._queue(db,'pc1','laser','remove',self.catalog_hash,receipt['revision'])
        self.core_report(core=CORE)
        self.assertEqual(self.app_report()['jobs'],[])

    def test_pending_app_mutation_prevents_migration_and_legacy_scratch_is_suppressed(self):
        self.core_report(core=CORE);self.app_report()
        self.admin.order(self.app_order())
        self.core_report()
        with self.assertRaisesRegex(UpdateError,'migration_pending_apps'):
            self.service.order(self.order(self.target,revision=self.service.overview()['revision']))
        with self.service.transaction() as db:db.execute("UPDATE apps_jobs SET state='failed'")
        old_scratch=self.release(version='15.1.1+local.20261104.1',sequence=4,component='scratch')
        scratch_order=self.order(old_scratch,component='scratch',revision=self.service.overview()['revision'])
        self.service.order(scratch_order)
        _,receipt=self.authorize()
        offered=self.core_report()['desired']
        self.assertNotIn('scratch',{item['component'] for item in offered})
        with self.assertRaisesRegex(UpdateError,'legacy_scratch_suppressed'):
            self.service.order(self.order(old_scratch,component='scratch',revision=self.service.overview()['revision']))

    def test_stale_inventory_versions_and_incomplete_entries_do_not_complete(self):
        _,receipt=self.authorize()
        self.core_report(core=CORE,result={'revision':receipt['revision'],'state':'succeeded','code':None})
        body={'schema':1,'capabilities':['apps-v1'],'updater_version':BRIDGE,'core_version':CORE,
              'installed':[{'id':name,'package':self.entries[name]['package'],'version':self.entries[name]['version'],'state':'installed'} for name in PRESERVED],
              'issues':[],'result':None}
        first=body['installed'][0];correct=first['version'];first['version']='0.1.0'
        self.admin.poll('pc1',body);self.assertIs(self.wire(receipt)['completed'],False)
        first.update(version=correct,state='incomplete')
        self.admin.poll('pc1',body);self.assertIs(self.wire(receipt)['completed'],False)
        first['state']='installed';self.admin.poll('pc1',body)
        self.assertIs(self.wire(receipt)['completed'],True)

    def test_new_identity_cannot_read_or_inherit_binding_on_same_role_rebind(self):
        _,receipt=self.authorize()
        register=self.root/'registry.json';value=json.loads(register.read_text(encoding='utf-8'))
        value['enheder']['pc1']['token_sha256']='d'*64
        register.write_text(json.dumps(value),encoding='utf-8')
        with self.assertRaises(UpdateError):self.wire(receipt)
        self.service.bind(self.binding_body(expected_revision=self.service.overview()['revision']))
        self.assertFalse(self.active())
        self.assertEqual(self.service.overview()['devices'][0]['installed'],[])
        with self.assertRaises(UpdateError):self.wire(receipt)

    def test_rebind_and_revocation_clear_identity_owned_migration_state(self):
        _,receipt=self.authorize()
        self.service.bind(self.binding_body(expected_revision=self.service.overview()['revision'],role='server',platform='windows-amd64'))
        with self.assertRaises(UpdateError):self.wire(receipt)
        self.service.bind(self.binding_body(expected_revision=self.service.overview()['revision']))
        self.assertEqual(self.service.overview()['devices'][0]['installed'],[])
        self.assertFalse(self.active())
        _,receipt=self.authorize()
        revoke_device_updates(self.root,'pc1',revoked_token_sha256=self.registry.identitet('pc1'))
        with self.assertRaises(UpdateError):self.wire(receipt)
        with self.service.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM migrations WHERE device=?',('pc1',)).fetchone()[0],0)


if __name__=='__main__':unittest.main()
