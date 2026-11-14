"""The package contract must follow the canonical verifier in each shipped layout."""
from pathlib import Path as _LayoutPath
import sys as _layout_sys
_LAYOUT_ROOT = next(p for p in _LayoutPath(__file__).resolve().parents if (p / 'REPOSITORY-LAYOUT.json').is_file())
_layout_sys.path[:0] = [str(_LAYOUT_ROOT), str(_LAYOUT_ROOT / 'system')]
from repository import source_path, repo_root
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = repo_root(__file__)
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
            for name in (*FILES, 'app/__init__.py', 'app/simple_release.py',
                         'app/app_catalog.py', 'app/app_packages.py',
                         'apps/3dprint/validate_build.py', 'repository.py', 'REPOSITORY-LAYOUT.json'):
                target = root / source_path(name, ROOT).relative_to(ROOT)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source_path(name, ROOT), target)
            canonical = source_path('app/simple_release.py', root)
            canonical.write_text(canonical.read_text().replace('makervaerkstedet-scratch', SENTINEL))
            script = '''import runpy, sys
from pathlib import Path
root=Path(sys.argv[1]); sys.path[:0]=[str(root), str(root/'system'), str(root/'system/linux-elevhub/packaging')]
from repository import source_path
for name in sys.argv[2:]:
    module=runpy.run_path(str(source_path(name, root)))
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
            shutil.copyfile(source_path('linux-elevhub/server/elevhub/simple_updates.py', ROOT), root / 'hub/simple_updates.py')
            source = (source_path('app/simple_release.py', ROOT)).read_text().replace('makervaerkstedet-scratch', SENTINEL)
            (root / 'app/simple_release.py').write_text(source)
            script = "import runpy,sys; m=runpy.run_path(sys.argv[1]); assert m['DEBIAN_PACKAGES']['scratch'][0] == 'makervaerkstedet-contract-test'"
            result = subprocess.run([sys.executable, '-I', '-c', script, str(root / 'hub/simple_updates.py')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_installed_payload_and_updater_read_sibling_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ('install_payload.py', 'bootstrap_existing.py'):
                shutil.copyfile(source_path('linux-elevhub/installation/debian', ROOT) / name, root / name)
            shutil.copyfile(source_path('linux-elevhub/packaging/apt_sandbox.py', ROOT), root / 'apt_sandbox.py')
            source = (source_path('app/simple_release.py', ROOT)).read_text().replace('makervaerkstedet-scratch', SENTINEL)
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
