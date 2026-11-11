"""Producer source tests; mocked native tools do not prove apt/GPG execution."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from scripts import build_apt_repository as apt
from scripts import build_simple_feed as feed


class ProducerTests(unittest.TestCase):
    def test_flat_repository_omits_valid_until_and_expiry_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); home=root/'private'; home.mkdir()
            package=root/'input.deb'; package.write_bytes(b'package')
            gpg=root/'gpg'; gpg.write_bytes(b'source-fixture')
            entry={'path':str(package),'sha256':apt.sha(package),'size':7,
                   'package':'makervaerkstedet-elev','version':'2.0.0~20261104.3','architecture':'all'}
            spec={'schema':1,'packages':[entry],'test_repository':True,'install_gate':{'path':'synthetic','sha256':'a'*64}}
            def output(arguments,**kwargs):
                if arguments[0]=='dpkg-deb': return entry[arguments[-1].lower()]+'\n'
                if arguments[0]=='apt-ftparchive': return b'Date: Wed, 04 Nov 2026 00:00:00 UTC\n'
                return b'synthetic-public-key'
            def run(arguments,**kwargs):
                if arguments[0]=='dpkg-scanpackages': return subprocess.CompletedProcess(arguments,0,b'Package: makervaerkstedet-elev\n')
                Path(arguments[arguments.index('--output')+1]).write_bytes(b'synthetic-signature')
                return subprocess.CompletedProcess(arguments,0)
            with patch.object(apt,'validate_install_gate',return_value='a'*64),patch.object(apt.subprocess,'check_output',side_effect=output),patch.object(apt.subprocess,'run',side_effect=run):
                receipt=apt.build(spec,root/'repo',gpg_exe=gpg,gpg_sha256=apt.sha(gpg),gnupg_home=home,key_fingerprint='A'*40)
            self.assertNotIn(b'Valid-Until:',(root/'repo/Release').read_bytes())
            self.assertNotIn('expires_utc',receipt)
            with self.assertRaisesRegex(ValueError,'invalid_repository_inputs'):
                apt.build(dict(spec,expires_utc='2020-01-01T00:00:00Z'),root/'another',gpg_exe=gpg,gpg_sha256=apt.sha(gpg),gnupg_home=home,key_fingerprint='A'*40)

    def test_component_and_feed_producers_emit_exact_schema_without_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); repo=root/'repo'; (repo/'pool').mkdir(parents=True)
            artifact={'package':'makervaerkstedet-elev','version':'2.0.0~20261104.3','architecture':'all',
                      'sha256':hashlib.sha256(b'package').hexdigest(),'size':7}
            (repo/'pool/makervaerkstedet-elev_2.0.0~20261104.3_all.deb').write_bytes(b'package')
            for name in feed.REQUIRED:(repo/name).write_bytes(b'synthetic metadata')
            receipt={'schema':1,'packages':[artifact],'files':{p.relative_to(repo).as_posix():feed.sha(p) for p in repo.rglob('*') if p.is_file()}}; seen=[]
            def sign(path,**kwargs):
                Path(kwargs.get('signature',str(path)+'.minisig')).write_bytes(b'source-fixture')
            def manifest(raw,*args,**kwargs):
                value=json.loads(raw); seen.append(value)
                self.assertEqual(set(value),{'schema','repository','role','platform','component','version','sequence','artifacts'})
                return dict(value,_manifest_sha256=hashlib.sha256(raw).hexdigest())
            def descriptor(raw,*args,**kwargs):
                value=json.loads(raw)
                self.assertEqual(set(value),{'schema','repository','release_tag','sequence','snapshot'})
                return dict(value,_descriptor_sha256=hashlib.sha256(raw).hexdigest())
            record={'name':feed.ASSET,'sha256':'a'*64,'size':123}
            with patch.object(feed,'sign_minisign',side_effect=sign) as signer,patch.object(feed,'verify_manifest',side_effect=manifest),patch.object(feed,'verify_descriptor',side_effect=descriptor),patch.object(feed,'snapshot',return_value=record):
                feed.build(repo,root/'out',receipt,minisign_exe=root/'fixture',minisign_sha256='b'*64,public_key='fixture',release_tag='v2020.1',sequence=3)
                signature=next((repo/'manifests').glob('*.minisig'));before=signature.read_bytes()
                feed.build(repo,root/'retry',receipt,minisign_exe=root/'fixture',minisign_sha256='b'*64,public_key='fixture',release_tag='v2020.1',sequence=3)
                self.assertEqual(signer.call_count,3) # two descriptors, one immutable component signature
                self.assertEqual(signature.read_bytes(),before)
            self.assertEqual(len(seen),2)

    def test_snapshot_cannot_reseal_modified_or_added_repository_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);repo=root/'repo';(repo/'pool').mkdir(parents=True)
            for name in feed.REQUIRED:(repo/name).write_bytes(b'original metadata')
            (repo/'pool/makervaerkstedet-elev_1_all.deb').write_bytes(b'package')
            expected={p.relative_to(repo).as_posix():feed.sha(p) for p in repo.rglob('*') if p.is_file()}
            (repo/'Packages').write_bytes(b'modified metadata')
            with self.assertRaisesRegex(ValueError,'snapshot_original_input_mismatch'):
                feed.snapshot(repo,root/'modified.zip',expected_files=expected)
            self.assertFalse((root/'modified.zip').exists())
            (repo/'Packages').write_bytes(b'original metadata')
            (repo/'pool/makervaerkstedet-extra_1_all.deb').write_bytes(b'added')
            with self.assertRaisesRegex(ValueError,'nonpublic_snapshot_input'):
                feed.snapshot(repo,root/'additional.zip',expected_files=expected)
            self.assertFalse((root/'additional.zip').exists())


if __name__=='__main__': unittest.main()
