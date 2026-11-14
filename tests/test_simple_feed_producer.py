"""A modern public snapshot binds app metadata and native repository hashes."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from app import simple_release,simple_feed
from scripts import build_simple_feed as feed
from tests.test_app_catalog import descriptor,envelope


class ModernFeedTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.repo=self.root/'repo';(self.repo/'pool').mkdir(parents=True)
        self.app=descriptor();self.catalog=envelope(self.app);self.catalog['sequence']=7;self.catalog['apps'][0]['sequence']=7
        self.packages=[]
        for name,arch in [('makervaerkstedet-elev','all'),('makervaerkstedet-updater','amd64'),('makervaerkstedet-matchbox','amd64'),('makervaerkstedet-app-noter','all')]:
            artifact={'package':name,'version':'1.0.0','architecture':arch,'sha256':hashlib.sha256(b'package').hexdigest(),'size':7}
            (self.repo/'pool'/f'{name}_1.0.0_{arch}.deb').write_bytes(b'package');self.packages.append(artifact)
        self.catalog['apps'][0]['artifact'].update(sha256=self.packages[-1]['sha256'],size=7)
        self.receipt={'schema':2,'release_set':'app','app_descriptors':[self.app],'packages':self.packages}
        for name in feed.REQUIRED:(self.repo/name).write_bytes(b'native signed metadata')
        self.receipt['files']={path.relative_to(self.repo).as_posix():feed.sha(path) for path in self.repo.rglob('*') if path.is_file()}
        self.options=dict(minisign_exe=self.root/'minisign',minisign_sha256='b'*64,public_key='fixture',release_tag='app-noter-1.0.0',sequence=7)

    def pin(self,catalog=None):
        path=self.root/'catalog.json';path.write_text(json.dumps(self.catalog if catalog is None else catalog))
        return {'path':str(path),'sha256':feed.sha(path)}

    def sign(self,path,**kwargs):
        Path(kwargs.get('signature',str(path)+'.minisig')).write_bytes(b'fixture-signature')

    def signed_build(self,output,**kwargs):
        # Native signature boundary only; shared descriptor/manifest/catalog
        # validators and the ZIP/inventory construction all execute for real.
        with patch.object(feed,'sign_minisign',side_effect=self.sign),patch.object(simple_release,'_minisign'),patch.object(simple_feed,'_minisign'):
            return feed.build(self.repo,output,self.receipt,**self.options,**kwargs)

    def test_modern_snapshot_contains_strict_catalog_app_pool_and_legacy_base_manifests(self):
        result=self.signed_build(self.root/'out',app_catalog=self.pin())
        with zipfile.ZipFile(self.root/'out'/feed.ASSET) as archive:
            inventory=json.loads(archive.read('snapshot-inventory.json'))['files']
            catalog_names=[];base_components=set()
            for name in archive.namelist():
                if name.startswith('manifests/') and name.endswith('.json'):
                    value=json.loads(archive.read(name))
                    if value['schema']==2:catalog_names.append(name);self.assertEqual(value,self.catalog)
                    else:base_components.add(value['component'])
            self.assertEqual(base_components,{'studentcore','updater','matchbox'})
            self.assertEqual(len(catalog_names),1)
            self.assertEqual(inventory[catalog_names[0]]['sha256'],Path(catalog_names[0]).stem)
            self.assertIn(catalog_names[0].replace('.json','.minisig'),inventory)
            self.assertEqual(inventory['pool/makervaerkstedet-app-noter_1.0.0_all.deb']['sha256'],self.packages[-1]['sha256'])
        self.assertFalse(result['published'])

    def test_modern_feed_requires_pinned_catalog_before_writing_or_signing(self):
        with patch.object(feed,'sign_minisign') as signer,self.assertRaisesRegex(ValueError,'app_catalog_pin_required'):
            feed.build(self.repo,self.root/'out',self.receipt,**self.options)
        signer.assert_not_called();self.assertFalse((self.root/'out').exists())

    def test_catalog_cannot_rebind_descriptor_artifact_sequence_or_release_tag(self):
        variants=[]
        for key,value in [('sha256','0'*64),('size',8),('url',self.catalog['apps'][0]['artifact']['url'].replace('app-noter-1.0.0','wrong-tag'))]:
            changed=deepcopy(self.catalog);changed['apps'][0]['artifact'][key]=value;variants.append(changed)
        changed=deepcopy(self.catalog);changed['apps'][0]['descriptor']['description']='different';variants.append(changed)
        changed=deepcopy(self.catalog);changed['sequence']=6;variants.append(changed)
        changed=deepcopy(self.catalog);changed['apps']=[];variants.append(changed)
        for index,value in enumerate(variants):
            with self.subTest(index=index),patch.object(feed,'sign_minisign') as signer,self.assertRaises(ValueError):
                feed.build(self.repo,self.root/f'out-{index}',self.receipt,**self.options,app_catalog=self.pin(value))
            signer.assert_not_called();self.assertFalse((self.root/f'out-{index}').exists())

    def test_mutated_catalog_pin_and_extra_unreceipted_app_pool_fail_closed(self):
        pin=self.pin();Path(pin['path']).write_bytes(Path(pin['path']).read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError,'app_catalog_hash_mismatch'):
            self.signed_build(self.root/'changed-catalog',app_catalog=pin)
        (self.repo/'pool/makervaerkstedet-app-extra_1.0.0_all.deb').write_bytes(b'extra')
        with self.assertRaises(ValueError):self.signed_build(self.root/'extra-app',app_catalog=self.pin())

    def test_modern_immutable_catalog_signature_is_reused(self):
        pin=self.pin();self.signed_build(self.root/'out',app_catalog=pin)
        names={path.name:path.read_bytes() for path in (self.repo/'manifests').iterdir()}
        self.signed_build(self.root/'retry',app_catalog=pin)
        self.assertEqual(names,{path.name:path.read_bytes() for path in (self.repo/'manifests').iterdir()})

    def test_app_catalog_pool_allowance_is_explicit_not_a_general_package_allowlist(self):
        self.assertFalse(feed.allowed('pool/makervaerkstedet-app-noter_1.0.0_all.deb'))
        self.assertTrue(feed.allowed('pool/makervaerkstedet-app-noter_1.0.0_all.deb',app_packages={'makervaerkstedet-app-noter'}))
        self.assertFalse(feed.allowed('pool/curl_1.0.0_all.deb',app_packages={'curl'}))

    def test_base_release_needs_no_catalog_and_can_explicitly_sign_empty_catalog(self):
        app_package=self.packages.pop();self.receipt['release_set']='base';self.receipt['app_descriptors']=[]
        name='pool/makervaerkstedet-app-noter_1.0.0_all.deb';(self.repo/name).unlink();del self.receipt['files'][name]
        result=self.signed_build(self.root/'base')
        self.assertEqual({item['component'] for item in result['components']},{'studentcore','updater','matchbox'})
        self.catalog['apps']=[]
        result=self.signed_build(self.root/'base-with-catalog',app_catalog=self.pin())
        self.assertEqual({item['component'] for item in result['components']},{'studentcore','updater','matchbox','app-catalog'})

    def test_modern_scratch_is_only_in_schema2_catalog_not_a_legacy_scratch_manifest(self):
        old='pool/makervaerkstedet-app-noter_1.0.0_all.deb';new='pool/makervaerkstedet-scratch_1.0.0_all.deb'
        (self.repo/old).rename(self.repo/new);self.receipt['files'][new]=self.receipt['files'].pop(old)
        self.app.update(id='scratch',package='makervaerkstedet-scratch');self.app['project'].update(kind='scratch',extension='.sb3')
        self.packages[-1]['package']='makervaerkstedet-scratch'
        artifact=self.catalog['apps'][0]['artifact'];artifact['name']='makervaerkstedet-scratch_1.0.0_all.deb'
        artifact['url']='https://github.com/mdkmsuv/makervaerkstedet/releases/download/app-scratch-1.0.0/'+artifact['name']
        self.options['release_tag']='app-scratch-1.0.0'
        result=self.signed_build(self.root/'scratch',app_catalog=self.pin())
        self.assertEqual({item['component'] for item in result['components']},{'studentcore','updater','matchbox','app-catalog'})


if __name__=='__main__':unittest.main()
