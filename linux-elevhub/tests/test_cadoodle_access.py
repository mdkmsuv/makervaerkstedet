"""The native 3D entry must follow the same teacher access as the hub."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parents[1] / 'ansible/roles/kiosk/files'))
from cadoodle_vaerksted import Vaerksted


class NativeAccessTest(unittest.TestCase):
    def workshop(self, opened, paused):
        workshop = Vaerksted.__new__(Vaerksted)
        workshop.bjaelke = SimpleNamespace(pause_overlay=SimpleNamespace(paused=paused), rod=Mock())
        workshop.aaben = Mock(return_value=opened)
        workshop.paa_start = Mock(return_value=True)
        workshop.vis_genvej = Mock()
        workshop.knap = Mock()
        workshop.skjul = Mock()
        workshop.aabn_gemt_projekt = Mock()
        workshop.aabn_projekthylde = Mock()
        return workshop

    def test_closed_3d_has_no_native_entry_and_existing_window_is_hidden(self):
        workshop = self.workshop(False, False)
        workshop.tjek()
        workshop.vis_genvej.assert_called_once_with(False)
        workshop.skjul.assert_called_once()

    def test_open_3d_returns_on_home(self):
        workshop = self.workshop(True, False)
        workshop.tjek()
        workshop.vis_genvej.assert_called_once_with(True)
        workshop.skjul.assert_not_called()

    def test_pause_hides_entry_without_hiding_existing_project(self):
        workshop = self.workshop(True, True)
        workshop.tjek()
        workshop.vis_genvej.assert_called_once_with(False)
        workshop.skjul.assert_not_called()
        # Programmatic invocations cannot start a new app during pause either.
        workshop.vis()
        workshop.print()
        workshop.start_cad()
        self.assertEqual(workshop.vis_genvej.call_count, 1)


if __name__ == '__main__':
    unittest.main()
