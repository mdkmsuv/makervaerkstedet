"""Package input locks contain exactly the local workshop manifest files."""
from pathlib import Path as _LayoutPath
import sys as _layout_sys
_LAYOUT_ROOT = next(p for p in _LayoutPath(__file__).resolve().parents if (p / 'REPOSITORY-LAYOUT.json').is_file())
_layout_sys.path[:0] = [str(_LAYOUT_ROOT), str(_LAYOUT_ROOT / 'system')]
from repository import source_path, repo_root
import hashlib
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = repo_root(__file__)
sys.path.insert(0, str(source_path('linux-elevhub/packaging', ROOT)))
sys.path.insert(0, str(source_path('linux-elevhub/projekter', ROOT)))
import prepare_inputs
from app_manifests import validate_manifest


class PackageManifests(unittest.TestCase):
    def test_real_input_locks_include_valid_versioned_workshops(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            scratch = root / 'scratch'; scratch.mkdir()
            (scratch / 'index.html').write_text('synthetic scratch', encoding='utf-8')
            old = root / 'old.json'
            old.write_text(json.dumps({'files': {'assets/scratch/index.html': hashlib.sha256((scratch/'index.html').read_bytes()).hexdigest()}}))
            out = root / 'output'
            argv = ['prepare_inputs', '--source-root', str(ROOT), '--fallback-root', str(ROOT),
                    '--scratch', str(scratch), '--matchbox', str(root/'unused'), '--legacy-manifest', str(old),
                    '--output-dir', str(out), '--components', 'core', 'scratch']
            with patch.object(sys, 'argv', argv): prepare_inputs.main()
            expected = {
                'makervaerkstedet-elev': {},
                'makervaerkstedet-scratch': {'scratch': ('http://127.0.0.1:8792/', None, False)}}
            for package, apps in expected.items():
                lock = json.loads((out / (package+'.json')).read_text())
                if package == 'makervaerkstedet-elev':
                    self.assertIn('usr/share/makervaerkstedet/templates/chromium-politik.json.j2',
                                  {entry['path'] for entry in lock['files']})
                    self.assertFalse(any('system' in Path(entry['path']).parts for entry in lock['files']))
                    self.assertFalse(any(Path(entry['source']).is_relative_to(ROOT/'apps') for entry in lock['files']))
                entries = {entry['path']: entry for entry in lock['files'] if entry['path'].startswith('usr/share/makervaerkstedet/apps/')}
                self.assertEqual(set(entries), {f'usr/share/makervaerkstedet/apps/{id}/app.json' for id in apps})
                for id, (url, delivery, internet) in apps.items():
                    entry = entries[f'usr/share/makervaerkstedet/apps/{id}/app.json']
                    data = Path(entry['source']).read_bytes()
                    self.assertEqual(hashlib.sha256(data).hexdigest(), entry['sha256'])
                    self.assertEqual(Path(entry['source']).parent, out)
                    value = validate_manifest(json.loads(data), id)
                    self.assertEqual((value['version'], value['start'], value['aflevering'], value['kraever_internet']),
                                     (lock['version'], url, delivery, internet))
                    self.assertTrue(value['offline'])
                    self.assertEqual(value['navn'], {'laser': 'Laser', 'folie': 'Folie', '3dprint': '3D-v\u00e6rksted', 'microbit': 'micro:bit', 'scratch': 'Scratch'}[id])
            # Version numbers change with every release; check their form and
            # that the updater requires the core it was built with.
            self.assertRegex(prepare_inputs.CORE_VERSION, r'^2\.1\.0~\d{8}\.\d+$')
            self.assertRegex(prepare_inputs.UPDATER_VERSION, r'^1\.1\.0~\d{8}\.\d+$')
            self.assertIn('makervaerkstedet-elev (>= 2.0.0~20261114.2)', prepare_inputs.SPECS['updater'][3])

class ScratchInventory(unittest.TestCase):
    def test_scratch_accepts_only_pinned_release_inventory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); scratch = root / 'scratch'; scratch.mkdir()
            asset = scratch / 'index.html'; asset.write_text('released content')
            receipt = root / 'scratch.inputs.json'
            receipt.write_text(json.dumps({'package':'makervaerkstedet-scratch',
                'version':'15.1.1+local.20261104.1', 'architecture':'all',
                'sha256':'1ae6af12a62c0bb4111967473563f99a1fa97e2cf0c7a02711fa608679713c50',
                'files':[{'path':'usr/local/share/elevhub/apps/scratch/index.html',
                          'sha256':hashlib.sha256(asset.read_bytes()).hexdigest(),'mode':420}]}))
            old = root/'old.json'; old.write_text(json.dumps({'files':{}}))
            out = root/'output'
            argv=['prepare_inputs','--source-root',str(ROOT),'--fallback-root',str(ROOT),
                  '--scratch',str(scratch),'--scratch-receipt',str(receipt),'--matchbox',str(root/'unused'),
                  '--legacy-manifest',str(old),'--output-dir',str(out),'--components','scratch']
            with patch.object(sys,'argv',argv), patch.object(prepare_inputs,'SCRATCH_RECEIPT_SHA256',hashlib.sha256(receipt.read_bytes()).hexdigest()):
                prepare_inputs.main()
            lock=json.loads((out/'makervaerkstedet-scratch.json').read_text())
            self.assertEqual(lock['provenance']['scratch_receipt_sha256'],hashlib.sha256(receipt.read_bytes()).hexdigest())
            self.assertIn('usr/local/share/elevhub/apps/scratch/index.html',[e['path'] for e in lock['files']])
            asset.write_text('changed content')
            argv[argv.index('--output-dir')+1] = str(root/'changed-output')
            with patch.object(sys,'argv',argv), patch.object(prepare_inputs,'SCRATCH_RECEIPT_SHA256',hashlib.sha256(receipt.read_bytes()).hexdigest()), self.assertRaisesRegex(ValueError,'historical_input_hash_mismatch'):
                prepare_inputs.main()
            receipt.write_text('{}')
            with patch.object(sys,'argv',argv), patch.object(prepare_inputs,'SCRATCH_RECEIPT_SHA256','0'*64), self.assertRaisesRegex(ValueError,'scratch_receipt_hash_mismatch'):
                prepare_inputs.main()

@unittest.skipUnless(hasattr(os, 'geteuid') and os.geteuid() == 0, 'build workflow requires Linux root')
class BuildWorkflow(unittest.TestCase):
    def run_workflow(self, release_set, app=None):
        from tests.test_build_source_export import run_workflow_case
        temporary=tempfile.TemporaryDirectory(prefix='mkv-source-workflow-')
        self.addCleanup(temporary.cleanup)
        result,work,calls=run_workflow_case(Path(temporary.name),release_set,app)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads((work/'report/source.json').read_text(encoding='utf-8'))['mode'],'git')
        release=json.loads((work/'debian/release-build.json').read_text(encoding='utf-8'))
        self.assertEqual(release['release_set'],release_set)
        self.assertEqual(len(list((work/'debian').glob('*.deb'))),len(release['packages']))
        self.assertFalse((work/'debian/makervaerkstedet-matchbox_unrelated_amd64.deb').exists())
        self.assertEqual(calls[-1]['script'],'build_release.py')
        self.assertIn('--input-locks',calls[-1]['args'])
        return work,calls,release

    def test_default_base_has_exactly_three_packages_and_no_app_build(self):
        work,calls,release=self.run_workflow('base')
        self.assertEqual(set(release['packages']),{'makervaerkstedet-elev','makervaerkstedet-updater','makervaerkstedet-matchbox'})
        self.assertFalse(any(call['script']=='prepare_app_inputs.py' for call in calls))
        self.assertFalse((work/'scratch-baseline').exists())

    def test_build_workflow_produces_scratch_instead_of_copying_old_archive(self):
        work,calls,release=self.run_workflow('app','scratch')
        self.assertEqual(len(release['packages']),4)
        self.assertIn('makervaerkstedet-scratch',release['packages'])
        scratch=list((work/'debian').glob('makervaerkstedet-scratch_*.deb'))
        self.assertEqual(len(scratch),1)
        self.assertEqual(scratch[0].read_bytes(),b'built makervaerkstedet-scratch')
        apps=[call for call in calls if call['script']=='prepare_app_inputs.py']
        self.assertEqual(len(apps),1)
        self.assertIn('--scratch-lock',apps[0]['args'])
        self.assertTrue((work/'scratch-baseline/makervaerkstedet-scratch.json').is_file())

    def test_explicit_migration_builds_all_five_independent_apps(self):
        _,calls,release=self.run_workflow('migration')
        self.assertEqual(len(release['packages']),8)
        apps={call['args'][call['args'].index('--app')+1] for call in calls if call['script']=='prepare_app_inputs.py'}
        self.assertEqual(apps,{'laser','folie','3dprint','scratch','microbit'})

if __name__ == '__main__': unittest.main()
