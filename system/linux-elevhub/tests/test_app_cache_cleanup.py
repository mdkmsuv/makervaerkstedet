"""App completion releases its archives without touching other packages/data."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / 'REPOSITORY-LAYOUT.json').is_file())
sys.path[:0] = [str(ROOT), str(ROOT / 'system'), str(ROOT / 'system/app'),
               str(ROOT / 'system/linux-elevhub/administration')]
import apt_client as client
from tests.test_app_catalog import descriptor


class AppCacheCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache = self.root / 'archives'
        self.cache.mkdir(mode=0o700)
        self.app = descriptor(project=False)
        self.commands = client.Commands(options=[], app_root=self.root / 'apps')
        self.commands.archives_root = self.cache

    def test_install_and_removal_clear_only_this_apps_old_and_current_archives(self):
        for removed in (False, True):
            with self.subTest(removed=removed):
                old = self.cache / 'makervaerkstedet-app-noter_0.9.0_all.deb'
                current = self.cache / 'makervaerkstedet-app-noter_1.0.0_all.deb'
                other = self.cache / 'makervaerkstedet-app-noter-extra_1.0.0_all.deb'
                for file in (old, current, other): file.write_bytes(b'synthetic archive')
                with patch.object(self.commands, 'activate'):
                    self.commands.activate_app('2.1.0~20261114.1', self.app, removed=removed)
                self.assertFalse(old.exists())
                self.assertFalse(current.exists())
                self.assertEqual(other.read_bytes(), b'synthetic archive')

    def test_cache_symlink_never_removes_its_target(self):
        outside = self.root / 'preserve.deb'
        outside.write_bytes(b'preserve')
        link = self.cache / 'makervaerkstedet-app-noter_1.0.0_all.deb'
        link.symlink_to(outside)
        with patch.object(self.commands, 'activate'), self.assertRaises(ValueError):
            self.commands.activate_app('2.1.0~20261114.1', self.app, removed=True)
        self.assertEqual(outside.read_bytes(), b'preserve')
        self.assertTrue(link.is_symlink())

    def test_app_activation_keeps_console_login_running(self):
        with patch.object(self.commands,'activate') as activate:
            self.commands.activate_app('2.1.0~20261115.1',self.app,removed=True)
        self.assertTrue(activate.call_args.kwargs.get('app_only',False))

    def test_absent_platform_units_are_skipped_but_present_rules_are_restarted(self):
        for load_state in ('not-found', 'loaded'):
            with self.subTest(load_state=load_state):
                def call(argv, **kwargs):
                    if '--property=LoadState' in argv:
                        return load_state + '\n' if argv[-1] == 'elevhub-regler.service' else 'loaded\n'
                    return ''
                with patch.object(self.commands, 'call', side_effect=call) as calls, patch.object(client, 'wait_ready'):
                    self.commands.activate('2.1.0~20261114.1')
                restart = next(item.args[0] for item in calls.call_args_list if 'try-restart' in item.args[0])
                self.assertEqual('elevhub-regler.service' in restart, load_state == 'loaded')
                self.assertIn('elevhub-lokal.service', restart)
                self.assertIn('elevhub-design.service', restart)


if __name__ == '__main__': unittest.main()
