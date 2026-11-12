"""The package contract must follow the canonical verifier in each shipped layout."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SENTINEL = 'makervaerkstedet-contract-test'
FILES = ('linux-elevhub/installation/debian/build_iso.py',
         'linux-elevhub/installation/debian/install_payload.py',
         'linux-elevhub/installation/debian/bootstrap_existing.py',
         'linux-elevhub/packaging/build_debian.py',
         'linux-elevhub/packaging/check_install.py',
         'linux-elevhub/packaging/prepare_inputs.py',
         'linux-elevhub/packaging/apt_sandbox.py',
         'linux-elevhub/server/elevhub/simple_updates.py')

class ComponentContract(unittest.TestCase):
    def test_source_consumers_read_canonical_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in (*FILES, 'app/simple_release.py'):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, target)
            canonical = root / 'app/simple_release.py'
            canonical.write_text(canonical.read_text().replace('makervaerkstedet-scratch', SENTINEL))
            script = '''import runpy, sys
from pathlib import Path
root=Path(sys.argv[1]); sys.path.insert(0,str(root/'linux-elevhub/packaging'))
for name in sys.argv[2:]:
    module=runpy.run_path(str(root/name))
    values=module.get('PACKAGES', module.get('DEBIAN_PACKAGES'))
    if name.endswith('simple_updates.py'): values=dict(values)
    if values is not None:
        assert 'makervaerkstedet-contract-test' in str(values), name
    if 'SPECS' in module:
        assert module['SPECS']['scratch'][0] == 'makervaerkstedet-contract-test', name
'''
            result = subprocess.run([sys.executable, '-c', script, str(root), *FILES[:-2], FILES[-1]], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_windows_hub_reads_bundled_contract_without_sys_path_lookup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'hub').mkdir()
            (root / 'app').mkdir()
            shutil.copyfile(ROOT / 'linux-elevhub/server/elevhub/simple_updates.py', root / 'hub/simple_updates.py')
            source = (ROOT / 'app/simple_release.py').read_text().replace('makervaerkstedet-scratch', SENTINEL)
            (root / 'app/simple_release.py').write_text(source)
            script = "import runpy,sys; m=runpy.run_path(sys.argv[1]); assert m['DEBIAN_PACKAGES']['scratch'][0] == 'makervaerkstedet-contract-test'"
            result = subprocess.run([sys.executable, '-I', '-c', script, str(root / 'hub/simple_updates.py')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_installed_payload_and_updater_read_sibling_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ('install_payload.py', 'bootstrap_existing.py'):
                shutil.copyfile(ROOT / 'linux-elevhub/installation/debian' / name, root / name)
            shutil.copyfile(ROOT / 'linux-elevhub/packaging/apt_sandbox.py', root / 'apt_sandbox.py')
            source = (ROOT / 'app/simple_release.py').read_text().replace('makervaerkstedet-scratch', SENTINEL)
            (root / 'simple_release.py').write_text(source)
            script = '''import runpy,sys
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root))
for name in ('install_payload.py','bootstrap_existing.py'):
 assert 'makervaerkstedet-contract-test' in runpy.run_path(str(root/name))['PACKAGES'],name
'''
            result = subprocess.run([sys.executable, '-c', script, str(root)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

if __name__ == '__main__': unittest.main()
