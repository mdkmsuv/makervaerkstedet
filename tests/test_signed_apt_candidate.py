"""Candidate preparation validates exact native inputs before opening any key."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.release_qa import build_signed_apt_candidate as candidate
from scripts import build_apt_repository as publisher
from tests.test_app_catalog import descriptor


def lock_hash(lock):
    return hashlib.sha256(json.dumps(lock,sort_keys=True,separators=(',',':')).encode()).hexdigest()


class SignedAptCandidate(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.upstream=self.root/'upstream';self.output=self.root/'new-output'
        for directory in ('report','debian','locks'):(self.upstream/directory).mkdir(parents=True)

    def fixture(self,release_set='base'):
        apps=[]
        if release_set=='app':apps=[descriptor()]
        if release_set=='migration':
            apps=[descriptor(identity,8800+index) for index,identity in enumerate(('laser','folie','3dprint','scratch','microbit'))]
            apps[3]['package']='makervaerkstedet-scratch'
        identities=[('makervaerkstedet-elev','all'),('makervaerkstedet-updater','amd64'),('makervaerkstedet-matchbox','amd64')]
        if release_set=='legacy':identities.append(('makervaerkstedet-scratch','all'))
        identities.extend((app['package'],app['architecture']) for app in apps)
        entries={}
        for name,arch in identities:
            app=next((app for app in apps if app['package']==name),None)
            version=app['version'] if app else '2.1.0~20261114.1'
            filename=f'{name}_{version}_{arch}.deb';archive=self.upstream/'debian'/filename
            archive.write_bytes(('synthetic pre-signing package '+name).encode())
            digest=candidate.sha(archive)
            entries[name]=dict(file=filename,version=version,architecture=arch,sha256=digest)
            if release_set=='legacy':continue
            files=[dict(source='/reviewed/public.txt',path=f'usr/share/doc/{name}/public.txt',sha256='a'*64,mode=420)]
            if app:
                data=(json.dumps(app,sort_keys=True,ensure_ascii=False)+'\n').encode()
                files.append(dict(source='/reviewed/app.json',path=f'usr/share/makervaerkstedet/apps/{app["id"]}/app.json',sha256=hashlib.sha256(data).hexdigest(),mode=420))
            lock=dict(schema='makervaerkstedet.debian-inputs.v2' if app else 'makervaerkstedet.debian-inputs.v1',
                      package=name,version=version,architecture=arch,depends=[],files=files,provenance={'source':'synthetic reviewed input'})
            if app:lock['app_descriptor']=app
            (self.upstream/'locks'/(name+'.json')).write_text(json.dumps(lock),encoding='utf-8')
            receipt=dict(package=name,version=version,architecture=arch,bytes=archive.stat().st_size,
                         sha256=digest,input_lock_sha256=lock_hash(lock),provenance=lock['provenance'],
                         files=[{key:value for key,value in item.items() if key!='source'} for item in files])
            archive.with_suffix('.inputs.json').write_text(json.dumps(receipt),encoding='utf-8')
        gate=dict(schema=1 if release_set=='legacy' else 2,target='debian13-amd64-empty-status',packages=entries,
                  all_packages_simulated=True,actual_install=False,isolated_paths=44,apt_config_sha256='b'*64,
                  debian_keyring_sha256=publisher.DEBIAN13_KEYRING_SHA256)
        if release_set!='legacy':gate['release_set']=release_set
        self.write_gate(gate)
        return gate

    def write_gate(self,gate):
        (self.upstream/'report/install-simulation.json').write_text(json.dumps(gate),encoding='utf-8')

    def prepare(self):
        return candidate.prepare_inputs(self.upstream,self.output,path_mapper=lambda path:str(Path(path).absolute()))

    def test_all_exact_sets_prepare_without_writes_or_signing(self):
        for release_set,count in (('base',3),('app',4),('migration',8),('legacy',4)):
            with self.subTest(release_set=release_set):
                for directory in ('debian','locks'):
                    for path in (self.upstream/directory).iterdir():path.unlink()
                self.fixture(release_set)
                spec,copies=self.prepare()
                self.assertEqual(len(spec['packages']),count)
                self.assertEqual(len(copies),count+1)
                self.assertFalse(self.output.exists())
                self.assertFalse(spec['test_repository'])
                self.assertEqual(spec['schema'],1 if release_set=='legacy' else 2)
                if release_set!='legacy':
                    self.assertEqual(spec['release_set'],release_set)
                    self.assertEqual(len(spec['app_descriptors']),count-3)
                else:self.assertNotIn('app_descriptors',spec)

    def test_missing_extra_and_ambiguous_locks_fail(self):
        self.fixture('app');lock=next((self.upstream/'locks').glob('*app-noter.json'))
        original=lock.read_bytes()
        lock.unlink()
        with self.assertRaises(ValueError):self.prepare()
        lock.write_bytes(original)
        extra=lock.with_name('unexpected.json');extra.write_bytes(original)
        with self.assertRaises(ValueError):self.prepare()

    def test_tampered_descriptor_is_not_signed_using_old_package_receipt(self):
        self.fixture('app');lock=self.upstream/'locks/makervaerkstedet-app-noter.json'
        value=json.loads(lock.read_bytes());value['app_descriptor']['description']='Unreviewed replacement'
        lock.write_text(json.dumps(value))
        with self.assertRaises(ValueError):self.prepare()

    def test_new_base_lock_hash_is_bound_but_reused_matchbox_inventory_is_portable(self):
        self.fixture()
        matchbox=next((self.upstream/'debian').glob('makervaerkstedet-matchbox*.inputs.json'))
        receipt=json.loads(matchbox.read_bytes());receipt['input_lock_sha256']='c'*64
        matchbox.write_text(json.dumps(receipt))
        self.assertEqual(len(self.prepare()[0]['packages']),3)
        core=next((self.upstream/'debian').glob('makervaerkstedet-elev*.inputs.json'))
        receipt=json.loads(core.read_bytes());receipt['input_lock_sha256']='c'*64
        core.write_text(json.dumps(receipt))
        with self.assertRaises(ValueError):self.prepare()

    def test_invalid_inputs_fail_before_signing_import_or_output_creation(self):
        self.fixture();(self.upstream/'debian/unapproved.deb').write_bytes(b'unapproved')
        with patch.object(candidate.importlib,'import_module',side_effect=AssertionError('signer reached')) as signer:
            with self.assertRaises(ValueError):
                candidate.main(['--source',str(candidate.ROOT),'--upstream',str(self.upstream),'--output',str(self.output)])
        signer.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_lock_identity_and_receipt_inventory_must_match(self):
        self.fixture();lock=self.upstream/'locks/makervaerkstedet-updater.json'
        original=json.loads(lock.read_bytes())
        for field,value in (('version','9.0'),('architecture','all'),('package','makervaerkstedet-scratch'),('files',[])):
            changed=deepcopy(original);changed[field]=value;lock.write_text(json.dumps(changed))
            with self.subTest(field=field),self.assertRaises(ValueError):self.prepare()
        lock.write_text(json.dumps(original))

    def test_missing_extra_and_tampered_packages_fail(self):
        gate=self.fixture();entry=next(iter(gate['packages'].values()));archive=self.upstream/'debian'/entry['file']
        original=archive.read_bytes();archive.unlink()
        with self.assertRaises(ValueError):self.prepare()
        archive.write_bytes(original+b' changed')
        with self.assertRaises(ValueError):self.prepare()
        archive.write_bytes(original);extra=archive.with_name('unapproved.deb');extra.write_bytes(b'extra')
        with self.assertRaises(ValueError):self.prepare()

    def test_receipt_traversal_and_nonregular_inputs_fail(self):
        gate=self.fixture();entry=next(iter(gate['packages'].values()))
        for filename in ('../outside.deb','/outside.deb','sub/file.deb','..\\outside.deb','C:outside.deb'):
            entry['file']=filename;self.write_gate(gate)
            with self.subTest(filename=filename),self.assertRaises(ValueError):self.prepare()

    def test_legacy_three_or_modern_receipt_disguised_as_legacy_fail(self):
        gate=self.fixture('legacy');gate['packages'].pop('makervaerkstedet-scratch');self.write_gate(gate)
        with self.assertRaises(ValueError):self.prepare()
        gate['release_set']='base';self.write_gate(gate)
        with self.assertRaises(ValueError):self.prepare()

    def test_linked_or_directory_package_is_rejected(self):
        gate=self.fixture();archive=self.upstream/'debian'/next(iter(gate['packages'].values()))['file']
        archive.unlink();archive.mkdir()
        with self.assertRaises(ValueError):self.prepare()
        archive.rmdir()
        try:archive.symlink_to(self.upstream/'report/install-simulation.json')
        except OSError:return  # Windows without symlink privilege still checks directory refusal above.
        with self.assertRaises(ValueError):self.prepare()

    def test_duplicate_json_fields_in_gate_fail(self):
        self.fixture();path=self.upstream/'report/install-simulation.json'
        path.write_text(path.read_text().replace('"schema": 2','"schema": 2, "schema": 2'))
        with self.assertRaises(ValueError):self.prepare()


if __name__=='__main__':unittest.main()
