"""A new enrollment may not inherit another identity's outstanding authority."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from urllib.error import HTTPError
import io
from unittest.mock import patch
import test_app_migration_client as fixtures

M=fixtures.M


class IdentityTests(unittest.TestCase):
    signed=fixtures.MigrationClientTests.signed
    make_catalog=fixtures.MigrationClientTests.make_catalog
    bind=fixtures.MigrationClientTests.bind

    def setUp(self):fixtures.MigrationClientTests.setUp(self)
    def test_new_identity_archives_pending_core_and_app_jobs_but_keeps_publisher_trust(self):
        self.transport.identity_sha256='a'*64
        self.client.tick()
        from app_operations import AppClient,Executor
        apps=Executor(self.state,self.commands)
        apps.bind_identity('a'*64)
        apps.ledger['jobs']['old-job']={'phase':'verified'}
        apps.ledger['outbox']=[{'job_id':'old-job','revision':90,'state':'succeeded','code':None}]
        apps.save()
        core_water=deepcopy(self.client.ledger['highwater']);app_water=deepcopy(apps.ledger['highwater'])
        self.transport.identity_sha256='b'*64;self.transport.response['desired']=[]
        self.client=M.Client(self.state,self.transport,self.commands,minisign=self.tool,public_key=self.key)
        self.client.tick()
        self.assertFalse(self.client.migration_pending)
        self.assertEqual(self.client.ledger['jobs'],{})
        self.assertEqual(self.client.ledger['outbox'],[])
        self.assertEqual(self.client.ledger['highwater'],core_water)
        self.transport.poll_apps=lambda body: {'schema':1,'jobs':[]}
        AppClient(self.client,manifest_root=self.root/'empty-manifests').tick()
        current=Executor(self.state,self.commands).ledger
        self.assertEqual(current['jobs'],{});self.assertEqual(current['outbox'],[])
        self.assertEqual(current['highwater'],app_water)
        self.assertEqual(set(current['approved']),fixtures.FOUR)
        self.assertEqual(set(self.commands.apps),fixtures.FOUR)
        snapshots=list((self.state/'identities').glob('*.json'))
        self.assertEqual(len(snapshots),2)
        archived=[json.loads(path.read_text()) for path in snapshots]
        self.assertTrue(any('studentcore:100' in value['jobs'] for value in archived))
        self.assertTrue(any('old-job' in value['jobs'] for value in archived))
        self.assertTrue(all(path.stat().st_mode&0o777==0o600 for path in snapshots))

    def test_core_ledger_retains_exact_legacy_shape_and_separate_private_scope(self):
        self.transport.identity_sha256='a'*64;self.client.tick()
        self.assertEqual(set(json.loads((self.state/'ledger.json').read_bytes())),
                         {'schema','jobs','outbox','highwater'})
        scope=self.state/'scope-core.json'
        self.assertEqual(json.loads(scope.read_bytes()),{'schema':1,'identity_sha256':'a'*64})
        self.assertEqual(scope.stat().st_mode&0o777,0o600)

    def test_first_bridge_run_sends_legacy_success_without_restarting_again(self):
        offer=self.signed('updater',version=fixtures.BRIDGE,revision=9)
        result={'component':'updater','revision':9,'state':'succeeded','code':None}
        self.client.ledger['jobs']['updater:9']={'phase':'complete','version':offer['version'],
                                                'manifest_sha256':offer['manifest_sha256']}
        self.client.ledger['outbox']=[result];self.client.save()
        self.transport.identity_sha256='a'*64;self.transport.response['desired']=[offer]
        self.client.tick()
        self.assertEqual(self.transport.sent[-1]['result'],{k:v for k,v in result.items() if k!='component'})
        self.assertEqual(self.commands.activations,0)
        self.assertEqual(self.commands.installs,0)

    def test_interrupted_scope_change_commits_empty_journal_before_new_authority(self):
        import identity_state
        self.transport.identity_sha256='a'*64;self.client.tick()
        water=deepcopy(self.client.ledger['highwater']);self.transport.response['desired']=[]
        self.transport.identity_sha256='b'*64;atomic=identity_state.atomic
        def interrupted(path,*args):
            if path.name=='scope-core.json':raise OSError('interrupted scope write')
            return atomic(path,*args)
        with patch.object(identity_state,'atomic',side_effect=interrupted):
            with self.assertRaisesRegex(OSError,'interrupted scope write'):self.client.tick()
        active=json.loads((self.state/'ledger.json').read_bytes())
        self.assertEqual(active['jobs'],{});self.assertEqual(active['outbox'],[])
        self.assertEqual(active['highwater'],water)
        self.assertEqual(json.loads((self.state/'scope-core.json').read_bytes())['identity_sha256'],'a'*64)
        self.client=M.Client(self.state,self.transport,self.commands,minisign=self.tool,public_key=self.key)
        self.client.tick()
        self.assertEqual(self.client.ledger['jobs'],{})
        self.assertEqual(json.loads((self.state/'scope-core.json').read_bytes())['identity_sha256'],'b'*64)
        self.assertEqual(len(list((self.state/'identities').glob('core-*.json'))),1)

    def test_unreleased_embedded_scope_is_normalized_without_replaying_jobs(self):
        self.transport.identity_sha256='a'*64;self.client.tick()
        self.client.ledger['identity_sha256']='a'*64;self.client.save()
        (self.state/'scope-core.json').unlink(missing_ok=True)
        jobs=deepcopy(self.client.ledger['jobs']);self.transport.response['desired']=[]
        self.client.tick()
        self.assertNotIn('identity_sha256',json.loads((self.state/'ledger.json').read_bytes()))
        self.assertEqual(set(self.client.ledger['jobs']),set(jobs))
        self.assertEqual(self.commands.installs,1)

    def test_same_token_rebind_requires_explicit_authenticated_not_found_status(self):
        self.transport.identity_sha256='a'*64;self.client.tick()
        get=self.transport.get
        def missing(path,limit):
            if path==self.binding_path:
                raise HTTPError('https://127.0.0.1'+path,404,'missing',{},io.BytesIO(b'{"error":"migration_not_found"}'))
            return get(path,limit)
        self.transport.get=missing;self.transport.response['desired']=[]
        self.client.tick()
        self.assertFalse(self.client.migration_pending)
        self.assertEqual(self.client.ledger['jobs']['studentcore:100']['migration']['phase'],'superseded')
        self.assertEqual(set(self.commands.apps),fixtures.FOUR)

    def test_generic_404_is_not_permission_to_forget_an_interrupted_migration(self):
        self.transport.identity_sha256='a'*64;self.client.tick();get=self.transport.get
        def missing(path,limit):
            if path==self.binding_path:
                raise HTTPError('https://127.0.0.1'+path,404,'missing',{},io.BytesIO(b'{"error":"unknown_route"}'))
            return get(path,limit)
        self.transport.get=missing;self.transport.response['desired']=[];self.client.tick()
        self.assertTrue(self.client.migration_pending)
        self.assertEqual(self.client.ledger['jobs']['studentcore:100']['migration']['phase'],'reported')

    def test_role_rebind_can_retire_an_intent_which_never_reached_native_install(self):
        self.transport.identity_sha256='a'*64
        self.commands.fail_download='makervaerkstedet-elev';self.client.tick()
        self.assertEqual(self.commands.installs,0)
        self.assertEqual(self.client.ledger['jobs']['studentcore:100']['migration']['phase'],'verified')
        get=self.transport.get
        def missing(path,limit):
            if path==self.binding_path:
                raise HTTPError('https://127.0.0.1'+path,404,'missing',{},io.BytesIO(b'{"error":"migration_not_found"}'))
            return get(path,limit)
        self.transport.get=missing;self.transport.response['desired']=[];self.client.tick()
        self.assertFalse(self.client.migration_pending)
        self.assertEqual(self.client.ledger['jobs']['studentcore:100']['migration']['phase'],'superseded')


if __name__=='__main__':unittest.main()
