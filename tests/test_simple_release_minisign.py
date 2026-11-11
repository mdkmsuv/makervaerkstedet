"""Actual minisign integration; requires explicitly supplied trusted tool and pin."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from app import simple_release as release
from app import simple_feed as feed

@unittest.skipUnless(os.environ.get('MKV_MINISIGN_EXE') and os.environ.get('MKV_MINISIGN_SHA256'), 'approved standalone minisign and pin not supplied')
class ActualMinisignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool=Path(os.environ['MKV_MINISIGN_EXE']);cls.pin=os.environ['MKV_MINISIGN_SHA256']
        if hashlib.sha256(cls.tool.read_bytes()).hexdigest()!=cls.pin:raise RuntimeError('integration verifier pin mismatch')
        cls.temp=tempfile.TemporaryDirectory(prefix='mkv-synthetic-signing-')
        cls.root=Path(cls.temp.name)
        if 'onedrive' in str(cls.root.resolve()).casefold():raise RuntimeError('synthetic private key must stay outside OneDrive')
        cls.private=cls.root/'synthetic.key';cls.public=cls.root/'synthetic.pub'
        subprocess.run([str(cls.tool),'-G','-W','-s',str(cls.private),'-p',str(cls.public)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        cls.key=cls.public.read_text().splitlines()[-1]
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def setUp(self):
        self.value={'schema':1,'repository':release.REPOSITORY,'role':'student','platform':'debian-amd64','component':'studentcore',
            'version':'2.0.0~20261104.2','sequence':7,
            'artifacts':[{'name':'core.deb','package':'makervaerkstedet-elev','version':'2.0.0~20261104.2','architecture':'all','size':7,'sha256':hashlib.sha256(b'package').hexdigest()}]}
        self.kw={'minisign_exe':self.tool,'minisign_sha256':self.pin,'public_key':self.key,'repository':release.REPOSITORY,
                 'role':'student','platform':'debian-amd64','component':'studentcore','now':datetime(2026,11,4,tzinfo=timezone.utc)}
    def sign(self):
        raw=json.dumps(self.value,indent=2).encode();path=self.root/'manifest.json';path.write_bytes(raw)
        subprocess.run([str(self.tool),'-S','-s',str(self.private),'-m',str(path)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        return raw,path.with_suffix('.json.minisig').read_bytes()
    def test_actual_sign_verify_exact_bytes(self):
        raw,sig=self.sign();verified=release.verify_manifest(raw,sig,**self.kw)
        self.assertEqual(verified['_manifest_sha256'],hashlib.sha256(raw).hexdigest())
    def test_actual_signature_rejects_modified_bytes(self):
        raw,sig=self.sign()
        with self.assertRaisesRegex(release.ReleaseVerificationError,'invalid_signature'):release.verify_manifest(raw+b' ',sig,**self.kw)
    def test_actually_signed_old_release_remains_valid(self):
        raw,sig=self.sign()
        self.kw['now']=datetime(2099,1,1,tzinfo=timezone.utc)
        verified=release.verify_manifest(raw,sig,**self.kw)
        self.assertEqual(verified['version'],'2.0.0~20261104.2')
    def test_actual_old_feed_acceptance_tamper_and_sequence_protection(self):
        self.value={'schema':1,'repository':release.REPOSITORY,'release_tag':'v2020.1','sequence':3,
                    'snapshot':{'name':feed.ASSET,'sha256':'a'*64,'size':123}}
        raw,sig=self.sign()
        kw={key:self.kw[key] for key in ('minisign_exe','minisign_sha256','public_key')}
        kw['now']=datetime(2099,1,1,tzinfo=timezone.utc)
        verified=feed.verify_descriptor(raw,sig,**kw)
        self.assertEqual(verified['release_tag'],'v2020.1')
        with self.assertRaisesRegex(release.ReleaseVerificationError,'invalid_signature'):
            feed.verify_descriptor(raw+b' ',sig,**kw)
        with self.assertRaisesRegex(release.ReleaseVerificationError,'feed_rollback_rejected'):
            feed.verify_descriptor(raw,sig,highest_sequence=4,**kw)
        with self.assertRaisesRegex(release.ReleaseVerificationError,'feed_sequence_collision'):
            feed.verify_descriptor(raw,sig,highest_sequence=3,highest_sha256='0'*64,**kw)
    def test_actually_signed_downgrade_needs_exact_explicit_authorization(self):
        raw,sig=self.sign();verified=release.verify_manifest(raw,sig,**self.kw);water={release.release_scope(verified):9}
        with self.assertRaisesRegex(release.ReleaseVerificationError,'rollback_authorization_required'):release.accept_sequence(verified,water)
        self.assertEqual(release.accept_sequence(verified,water,rollback_manifest_sha256=verified['_manifest_sha256']),9)
if __name__=='__main__':unittest.main()
