"""Inspect actual native archives before apt can run an app transaction."""
from pathlib import Path as _LayoutPath
import sys as _layout_sys
_LAYOUT_ROOT = next(p for p in _LayoutPath(__file__).resolve().parents if (p / 'REPOSITORY-LAYOUT.json').is_file())
_layout_sys.path[:0] = [str(_LAYOUT_ROOT), str(_LAYOUT_ROOT / 'system')]
from repository import source_path, repo_root
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
ROOT=repo_root(__file__)
sys.path[:0]=[str(ROOT),str(source_path('app', ROOT)),str(source_path('linux-elevhub/administration', ROOT))]
from tests.test_app_catalog import descriptor
archive=__import__('app_archive') if importlib.util.find_spec('app_archive') else None

class AppArchiveTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(archive,'Downloaded app archive inspection is missing')
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.app=descriptor()
    def deb(self,*,extra=None,script=False,depends=None,manifest=None):
        folder=self.root/'package';folder.mkdir();control=folder/'DEBIAN';control.mkdir()
        (control/'control').write_text('Package: makervaerkstedet-app-noter\nVersion: 1.0.0\nArchitecture: all\nMaintainer: Test <test@example.invalid>\nDescription: Synthetic archive\nDepends: '+(depends or 'makervaerkstedet-elev (>= 2.1.0~20261114.1)')+'\n')
        if script:
            path=control/'postinst';path.write_text('#!/bin/sh\nexit 0\n');path.chmod(0o755)
        metadata=folder/'usr/share/makervaerkstedet/apps/noter/app.json';metadata.parent.mkdir(parents=True)
        metadata.write_text(json.dumps(manifest or self.app))
        page=metadata.parent/'www/index.html';page.parent.mkdir();page.write_text('<html></html>')
        if extra:
            path=folder/extra;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('unrelated')
        output=self.root/'app.deb'
        subprocess.run(['dpkg-deb','--build','--root-owner-group',str(folder),str(output)],check=True,capture_output=True)
        return output
    def test_valid_archive_contains_bound_descriptor_and_only_owned_files(self):
        result=archive.inspect_deb(self.deb(),self.app)
        self.assertEqual(result['regular_files'],2)
    def test_maintainer_scripts_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'app_control_entry_forbidden'):archive.inspect_deb(self.deb(script=True),self.app)
    def test_other_program_files_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'app_archive_path_forbidden'):archive.inspect_deb(self.deb(extra='usr/bin/something'),self.app)
    def test_unapproved_dependencies_and_wrong_descriptor_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'app_archive_dependencies_mismatch'):archive.inspect_deb(self.deb(depends='curl'),self.app)
    def test_exact_descriptor_inside_archive_is_mandatory(self):
        value=dict(self.app,navn='A different descriptor')
        with self.assertRaisesRegex(ValueError,'app_archive_descriptor_mismatch'):archive.inspect_deb(self.deb(manifest=value),self.app)
    def test_installed_metadata_and_actual_absence_are_checked(self):
        import apt_client
        app_root=self.root/'apps';folder=app_root/'noter';folder.mkdir(parents=True)
        native=apt_client.Commands([],app_root=app_root)
        self.assertFalse(native.app_matches(self.app));self.assertTrue(native.app_absent(self.app))
        path=folder/'app.json';path.write_text(json.dumps(self.app))
        self.assertTrue(native.app_matches(self.app));self.assertFalse(native.app_absent(self.app))
        path.write_text(json.dumps(dict(self.app,version='0.9.0')))
        self.assertFalse(native.app_matches(self.app));self.assertFalse(native.app_absent(self.app))
    def test_native_plan_may_not_upgrade_basis(self):
        import apt_client
        self.assertTrue(callable(getattr(apt_client.Commands,'install_app',None)),'Restricted native app install is missing')
        class Restricted(apt_client.Commands):
            def __init__(inner):super().__init__([]);inner.argv=[]
            def call(inner,args,**kw):
                inner.argv.append(args)
                return 'Inst makervaerkstedet-elev [2.1.0] (2.2.0 localhost)\nInst makervaerkstedet-app-noter (1.0.0 local)\n'
        native=Restricted()
        with self.assertRaisesRegex(ValueError,'unsafe_app_install_plan'):native.install_app([self.deb()],self.app)
        self.assertEqual(len(native.argv),1)

    def test_expanded_install_space_is_checked_before_any_apt_apply(self):
        import apt_client
        class Native(apt_client.Commands):
            def call(inner,*a,**kw):raise AssertionError('apt reached despite zero install space')
        native=Native([],app_root=self.root/'target/apps')
        with patch.object(apt_client.shutil,'disk_usage',return_value=SimpleNamespace(free=0)):
            with self.assertRaisesRegex(ValueError,'insufficient_install_space'):native.install_app([self.deb()],self.app)

if __name__=='__main__':unittest.main()
