"""Descriptor source tests: injected crypto; actual primitive is tested separately."""
from copy import deepcopy
from datetime import datetime,timezone
import hashlib,json,importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
from app import simple_feed as feed
from app.simple_release import ReleaseVerificationError
class FeedTests(unittest.TestCase):
    def setUp(self):
        self.value={'schema':1,'repository':'mdkmsuv/makervaerkstedet','release_tag':'v2.0.0','sequence':3,
            'snapshot':{'name':feed.ASSET,'sha256':'a'*64,'size':123}}
    def verify(self,value=None,raw=None,**kwargs):
        with patch.object(feed,'_minisign'):
            return feed.verify_descriptor(raw or json.dumps(value or self.value).encode(),b'sig',minisign_exe=Path('fixture'),minisign_sha256='b'*64,public_key='fixture',now=datetime(2026,11,4,tzinfo=timezone.utc),**kwargs)
    def test_original_hash_and_fixed_repository_asset_url(self):
        raw=json.dumps(self.value,indent=4).encode();value=self.verify(raw=raw)
        self.assertEqual(value['_descriptor_sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual(value['_snapshot_url'],'https://github.com/mdkmsuv/makervaerkstedet/releases/download/v2.0.0/'+feed.ASSET)
    def test_wrong_repo_tag_asset_and_rollback_rejected(self):
        for field,bad,code in [('repository','evil/repo','feed_repository_mismatch'),('release_tag','../v2','invalid_release_tag')]:
            value=deepcopy(self.value);value[field]=bad
            with self.assertRaisesRegex(ReleaseVerificationError,code):self.verify(value=value)
        value=deepcopy(self.value);value['snapshot']['name']='other.zip'
        with self.assertRaisesRegex(ReleaseVerificationError,'invalid_snapshot_name'):self.verify(value=value)
        with self.assertRaisesRegex(ReleaseVerificationError,'feed_rollback_rejected'):self.verify(highest_sequence=4)
    def test_old_descriptor_has_no_age_gate_and_rejects_obsolete_field(self):
        with patch.object(feed,'_minisign'):
            feed.verify_descriptor(json.dumps(self.value).encode(),b'sig',minisign_exe=Path('fixture'),minisign_sha256='b'*64,public_key='fixture',now=datetime(2099,1,1,tzinfo=timezone.utc))
        self.value['expires_utc']='2026-12-01T00:00:00Z'
        with self.assertRaisesRegex(ReleaseVerificationError,'invalid_feed_fields'):self.verify()
    def test_equal_sequence_different_original_bytes_rejected(self):
        with self.assertRaisesRegex(ReleaseVerificationError,'feed_sequence_collision'):self.verify(highest_sequence=3,highest_sha256='0'*64)
        verified=self.verify();self.verify(highest_sequence=3,highest_sha256=verified['_descriptor_sha256'])
    def test_explicit_file_loader_does_not_need_app_on_sys_path(self):
        spec=importlib.util.spec_from_file_location('_legacy_feed_probe',Path(feed.__file__))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.assertEqual(module.REPOSITORY,'mdkmsuv/makervaerkstedet')
        with patch.object(module,'_minisign'):
            value=module.verify_descriptor(json.dumps(self.value).encode(),b'sig',minisign_exe=Path('fixture'),minisign_sha256='b'*64,public_key='fixture',now=datetime(2026,11,4,tzinfo=timezone.utc))
        self.assertEqual(value['snapshot']['name'],feed.ASSET)

    def test_duplicate_reserved_or_extra_fields_rejected(self):
        with self.assertRaisesRegex(ReleaseVerificationError,'duplicate_json_key'):self.verify(raw=b'{"schema":1,"schema":1}')
        value=deepcopy(self.value);value['snapshot']['_verified']=True
        with self.assertRaisesRegex(ReleaseVerificationError,'reserved_field'):self.verify(value=value)
        value=deepcopy(self.value);value['url']='https://evil.invalid'
        with self.assertRaisesRegex(ReleaseVerificationError,'invalid_feed_fields'):self.verify(value=value)
if __name__=='__main__':unittest.main()
