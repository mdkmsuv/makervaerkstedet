"""Package input locks contain exactly the local workshop manifest files."""
import hashlib
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'linux-elevhub/packaging'))
sys.path.insert(0, str(ROOT / 'linux-elevhub/projekter'))
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

class BuildWorkflow(unittest.TestCase):
    @unittest.skipUnless(hasattr(os, 'geteuid') and os.geteuid() == 0, 'build workflow requires Linux root')
    def test_build_workflow_produces_scratch_instead_of_copying_old_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); bindir = root / 'bin'; bindir.mkdir()
            released = root / 'released'; released.mkdir()
            (released / 'makervaerkstedet-matchbox_1_amd64.deb').write_bytes(b'synthetic')
            stub = bindir / 'python3'
            stub.write_text("""#!/usr/bin/python3
import json, os, pathlib, sys
args=sys.argv[1:]
if args[0]=='-c':
 sys.argv=['-c']+args[2:]; exec(args[1]); sys.exit(0)
script=args[1]
if script=='prepare_inputs.py':
 out=pathlib.Path(args[args.index('--output-dir')+1]); out.mkdir(exist_ok=True)
 for kind in args[args.index('--components')+1:args.index('--source-root')]:
  name={'core':'elev','scratch':'scratch','updater':'updater'}[kind]
  (out/('makervaerkstedet-'+name+'.json')).write_text(json.dumps({'version':'1','architecture':'all'}))
elif script=='build_debian.py':
 pathlib.Path(args[args.index('--output')+1]).write_bytes(b'built')
elif script=='check_install.py':
 paths=args[args.index('--packages')+1:args.index('--debian-keyring')]
 assert len(paths)==4 and all(pathlib.Path(p).is_file() for p in paths), paths
else: raise AssertionError(args)
""")
            stub.chmod(0o755)
            script = root / 'build.sh'
            script.write_text((ROOT/'linux-elevhub/packaging/build_from_source.sh').read_text(), encoding='utf-8')
            env = os.environ.copy(); env['PATH'] = str(bindir) + os.pathsep + env['PATH']
            result = subprocess.run(['bash', str(script), str(ROOT), str(root/'inputs'), str(root/'tools'), str(released), str(root/'work')], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root/'work/debian/makervaerkstedet-scratch_1_all.deb').is_file())

if __name__ == '__main__': unittest.main()
