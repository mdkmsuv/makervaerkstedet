"""Binding tests for the mandatory five-package native simulation receipt."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import build_apt_repository as apt


class InstallGateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.path=self.root/'release-build.json'
        self.packages=[{'package':name,'version':'1.0~test.1','architecture':arch,
                        'sha256':'a'*64,'size':7,'path':str(self.root/(name+'_1.0~test.1_'+arch+'.deb'))}
                       for name,arch in apt.DEBIAN_COMPONENTS.values()]
        self.receipt={'schema':1,'target':'debian13-amd64-empty-status',
                      'packages':{p['package']:{**{k:p[k] for k in ('version','architecture','sha256')},'file':Path(p['path']).name} for p in self.packages},
                      'all_five_simulated':True,'actual_install':False,'isolated_paths':44,
                      'apt_config_sha256':'b'*64,'debian_keyring_sha256':apt.DEBIAN13_KEYRING_SHA256}

    def record(self):
        self.path.write_text(json.dumps(self.receipt),encoding='utf-8')
        return {'path':str(self.path),'sha256':apt.sha(self.path)}

    def reject_before_signing(self,spec,code):
        with patch.object(apt.subprocess,'run') as signer,patch.object(apt.subprocess,'check_output') as native:
            with self.assertRaisesRegex(ValueError,code):
                apt.build(spec,self.root/'out',gpg_exe=self.root/'unused',gpg_sha256='d'*64,gnupg_home=self.root/'unused-home',key_fingerprint='A'*40)
            signer.assert_not_called();native.assert_not_called()
        self.assertFalse((self.root/'out').exists())

    def test_matching_exact_five_package_receipt_is_accepted(self):
        record=self.record()
        self.assertEqual(apt.validate_install_gate(record,self.packages),record['sha256'])

    def test_missing_failed_or_mutated_receipt_stops_before_any_native_tool(self):
        spec={'schema':1,'packages':self.packages,'test_repository':True}
        self.reject_before_signing(spec,'invalid_repository_inputs')
        self.receipt['all_five_simulated']=False
        spec['install_gate']=self.record()
        self.reject_before_signing(spec,'clean_debian13_simulation_required')
        self.receipt['all_five_simulated']=True
        spec['install_gate']=self.record();self.path.write_bytes(self.path.read_bytes()+b' ')
        self.reject_before_signing(spec,'install_gate_hash_mismatch')

    def test_wrong_package_hash_version_architecture_or_omitted_package_is_rejected(self):
        spec={'schema':1,'packages':self.packages,'test_repository':True,'install_gate':self.record()}
        for key,value in [('sha256','0'*64),('version','2.0'),('architecture','arm64'),('path',str(self.root/'renamed.deb'))]:
            changed=deepcopy(spec);changed['packages'][0][key]=value
            with self.subTest(key=key):self.reject_before_signing(changed,'install_gate_package_mismatch')
        changed=deepcopy(spec);changed['packages'].pop()
        self.reject_before_signing(changed,'exact_five_simulated_packages_required')

    def test_other_archive_keyring_cannot_satisfy_clean_simulation_gate(self):
        self.receipt['debian_keyring_sha256']='0'*64
        spec={'schema':1,'packages':self.packages,'test_repository':True,'install_gate':self.record()}
        self.reject_before_signing(spec,'debian_archive_keyring_pin_mismatch')


if __name__=='__main__':unittest.main()
