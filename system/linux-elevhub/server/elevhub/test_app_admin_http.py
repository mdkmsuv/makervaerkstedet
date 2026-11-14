"""Real HTTPS auth, bounded profile uploads and observed app channel."""
import json
import unittest
import uuid
import test_app_admin as domain
import test_simple_updates as existing


class AppHTTPTests(existing.Fixture, unittest.TestCase):
    setUpClass = classmethod(existing.UpdateHTTPTests.setUpClass.__func__)
    tearDownClass = classmethod(existing.UpdateHTTPTests.tearDownClass.__func__)
    request = existing.UpdateHTTPTests.request
    close_servers = existing.UpdateHTTPTests.close_servers
    publish = domain.AppAdminTests.publish
    legacy_report = domain.AppAdminTests.legacy_report
    inventory = domain.AppAdminTests.inventory
    app_body = domain.AppAdminTests.body

    def setUp(self):
        existing.UpdateHTTPTests.setUp(self)
        import app_admin
        from app import app_catalog
        from unittest.mock import patch
        self.crypto_app = patch.object(app_catalog.simple_release, '_minisign')
        self.crypto_app.start(); self.addCleanup(self.crypto_app.stop)
        self.admin = app_admin.AppAdmin(self.service, verifier=app_catalog.verify_catalog)
        self.service.app_admin = self.admin
        self.catalog_hash = self.publish()
        self.origin = 'https://localhost:' + str(self.teacher.server_port)
        self.auth = {'Cookie': self.cookie, 'Origin': self.origin}

    def test_teacher_login_origin_and_native_root_are_required(self):
        self.assertEqual(self.request(1, '/api/admin/apps')[0], 401)
        self.assertEqual(self.request(1, '/api/admin/apps', headers=self.auth)[0], 200)
        self.legacy_report()
        body = {'schema': 1, 'capabilities': ['apps-v1'], 'updater_version': '1.1.0~20261114.1',
                'core_version': '2.1.0~20261114.1', 'installed': [], 'issues': [], 'result': None}
        for role, expected in (('ui', 401), ('root', 200)):
            headers = {'Authorization': 'Bearer ' + self.tokens[role], 'X-Elevhub-Computer': 'pc1'}
            self.assertEqual(self.request(0, '/api/device/apps', body, headers)[0], expected)
        root = {'Authorization': 'Bearer ' + self.tokens['root'], 'X-Elevhub-Computer': 'pc1'}
        self.assertEqual(self.request(0, '/api/device/apps', body, root | {'Origin': 'null'})[0], 403)
        order = self.app_body()
        self.assertEqual(self.request(1, '/api/admin/apps/order', order, {'Cookie': self.cookie})[0], 403)
        self.assertEqual(self.request(1, '/api/admin/apps/order', order, self.auth)[0], 200)
        status, raw, _ = self.request(0, '/api/device/apps', body, root)
        self.assertEqual((status, json.loads(raw)['jobs'][0]['app_id']), (200, 'noter'))
        self.assertEqual(self.request(0, '/apt/apps/catalog/' + self.catalog_hash + '.json')[0], 200)
        self.assertEqual(self.request(0, '/apt/apps/catalog/' + '0'*64 + '.json')[0], 404)

    def test_migration_get_uses_native_root_auth_and_exact_immutable_wire(self):
        from test_app_migration import CORE,PRESERVED,native_report,publish_catalog
        self.catalog_hash=publish_catalog(self)
        target=self.release(version=CORE,sequence=3)
        self.service.poll('pc1',native_report())
        command=self.order(target,revision=self.service.overview()['revision'])
        receipt=self.service.order(command)
        path='/api/device/migration/'+target+'/'+str(receipt['revision'])
        root={'Authorization':'Bearer '+self.tokens['root'],'X-Elevhub-Computer':'pc1'}
        ui={'Authorization':'Bearer '+self.tokens['ui'],'X-Elevhub-Computer':'pc1'}
        self.assertEqual(self.request(0,path)[0],401)
        self.assertEqual(self.request(0,path,headers=ui)[0],401)
        self.assertEqual(self.request(0,path,headers={'Cookie':self.cookie})[0],401)
        self.assertEqual(self.request(0,path,headers=root|{'Origin':'null'})[0],403)
        self.assertEqual(self.request(0,path,headers=root,method='POST')[0],405)
        self.assertEqual(self.request(0,path,headers=root,method='HEAD')[0],405)
        status,raw,_=self.request(0,path,headers=root)
        self.assertEqual(status,200)
        expected={'schema':1,'core_manifest_sha256':target,'revision':receipt['revision'],
                  'catalog_sha256':self.catalog_hash,'apps':PRESERVED,'completed':False}
        self.assertEqual(json.loads(raw),expected)
        self.assertNotIn(self.registry.identitet('pc1').encode(),raw)
        self.assertEqual(self.request(0,path+'?other=1',headers=root)[0],404)
        self.assertEqual(self.request(0,path.rsplit('/',1)[0]+'/0',headers=root)[0],404)
        self.assertEqual(self.request(0,'/api/device/migration/'+'0'*64+'/1',headers=root)[0],404)
        ack={'revision':receipt['revision'],'state':'succeeded','code':None}
        self.assertEqual(self.request(0,'/api/device/updates',native_report(core=CORE,result=ack),root)[0],200)
        self.assertIs(json.loads(self.request(0,path,headers=root)[1])['completed'],False)
        from test_app_migration import BRIDGE
        inventory={'schema':1,'capabilities':['apps-v1'],'updater_version':BRIDGE,'core_version':CORE,
            'installed':[{'id':name,'package':self.entries[name]['package'],'version':self.entries[name]['version'],'state':'installed'} for name in PRESERVED],
            'issues':[],'result':None}
        self.assertEqual(self.request(0,'/api/device/apps',inventory,root)[0],200)
        status,raw,_=self.request(0,path,headers=root)
        self.assertEqual((status,json.loads(raw)),(200,expected|{'completed':True}))

    def test_stopping_migration_retries_requires_teacher_origin_and_preserves_native_status(self):
        from test_app_migration import CORE,native_report,publish_catalog
        publish_catalog(self);target=self.release(version=CORE,sequence=3)
        self.service.poll('pc1',native_report())
        receipt=self.service.order(self.order(target,revision=self.service.overview()['revision']))
        path='/api/admin/updates/cancel-migration'
        body={'command_id':str(uuid.uuid4()),'expected_revision':self.service.overview()['revision'],
              'device':'pc1','core_manifest_sha256':target,'migration_revision':receipt['revision']}
        self.assertEqual(self.request(1,path,body)[0],401)
        self.assertEqual(self.request(1,path,body,{'Cookie':self.cookie})[0],403)
        self.assertEqual(self.request(1,path,body,self.auth|{'Origin':'https://foreign.invalid'})[0],403)
        self.assertEqual(self.request(1,path,headers=self.auth)[0],405)
        one=self.request(1,path,body,self.auth);two=self.request(1,path,body,self.auth)
        self.assertEqual((one[0],two[0]),(200,200));self.assertEqual(json.loads(one[1]),json.loads(two[1]))
        root={'Authorization':'Bearer '+self.tokens['root'],'X-Elevhub-Computer':'pc1'}
        status,raw,_=self.request(0,'/api/device/migration/'+target+'/'+str(receipt['revision']),headers=root)
        self.assertEqual((status,json.loads(raw)),(404,{'error':'migration_not_found'}))


    def test_profile_editor_is_authenticated_origin_checked_and_delivers_long_name(self):
        path = '/api/admin/school-profile'
        self.assertEqual(self.request(1, path)[0], 401)
        status, raw, _ = self.request(1, path, headers=self.auth)
        self.assertEqual(status, 200)
        value = json.loads(raw)
        body = {'command_id': str(uuid.uuid4()), 'expected_revision': value['revision'],
                'profile': {'school_name': 'S'*100, 'colour': '#e7bb40', 'font': 'standard',
                            'logo_mode': 'keep', 'logo_png': None}}
        self.assertEqual(self.request(1, path, body, {'Cookie': self.cookie})[0], 403)
        self.assertEqual(self.request(1, path, body, self.auth)[0], 200)
        status, raw, _ = self.request(1, '/api/school-appearance', headers=self.auth)
        self.assertEqual((status, json.loads(raw)['school_name']), (200, 'S'*100))
        self.assertEqual(self.request(1, path, body, self.auth)[0], 200)
        body['command_id'] = str(uuid.uuid4())
        self.assertEqual(self.request(1, path, body, self.auth)[0], 409)

    def test_profile_distribution_uses_existing_device_root_auth_and_hashes(self):
        import hashlib
        path = '/api/device/school-profile'
        root = {'Authorization':'Bearer '+self.tokens['root'], 'X-Elevhub-Computer':'pc1'}
        self.assertEqual(self.request(0,path)[0],401)
        status,raw,_ = self.request(0,path,headers=root)
        self.assertEqual((status,json.loads(raw)),(200,{'schema':1,'enabled':False}))
        status,raw,_ = self.request(1,'/api/admin/school-profile',headers=self.auth)
        from test_school_profile_admin import png
        body={'command_id':str(uuid.uuid4()),'expected_revision':json.loads(raw)['revision'],
              'profile':{'school_name':'Skolen','colour':'#67bd23','font':'manrope','logo_mode':'replace','logo_png':png()}}
        self.assertEqual(self.request(1,'/api/admin/school-profile',body,self.auth)[0],200)
        status,raw,_=self.request(0,path,headers=root);value=json.loads(raw)
        self.assertEqual((status,value['manifest']['value']['font']),(200,'manrope'))
        self.assertEqual(self.request(0,path,headers=root|{'Origin':'null'})[0],403)
        self.assertEqual(len(value['assets']),3)
        for asset in value['assets']:
            self.assertEqual(self.request(0,asset['path'])[0],401)
            status,content,_=self.request(0,asset['path'],headers=root)
            self.assertEqual((status,len(content),hashlib.sha256(content).hexdigest()),(200,asset['size'],asset['sha256']))


if __name__ == '__main__':
    unittest.main()
