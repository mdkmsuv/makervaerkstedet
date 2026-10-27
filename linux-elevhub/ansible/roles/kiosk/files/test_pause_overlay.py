import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from pause_overlay import PauseOverlay


class FakeClock:
    def __init__(self):
        self.value = 10.0

    def __call__(self):
        return self.value


class TestOverlay(PauseOverlay):
    def _show(self, _title, _message):
        self._shown = True

    def _destroy(self):
        self._shown = False


class PauseOverlayTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.overlay = TestOverlay(object(), clock=self.clock, wall_clock=lambda: 0)

    def test_acknowledges_only_after_overlay_is_shown(self):
        self.overlay.update_pause({'paused': True, 'version': 4, 'message': 'Gem først', 'until': 100})
        self.assertEqual(self.overlay.acknowledgement(), {'version': 4, 'shown': True})
        self.overlay.update_pause({'paused': False, 'version': 5, 'message': '', 'until': None})
        self.assertEqual(self.overlay.acknowledgement(), {'version': 5, 'shown': False})

    def test_server_loss_keeps_lock_until_original_deadline(self):
        self.overlay.update_pause({'paused': True, 'version': 1, 'message': '', 'until': 100, 'remaining': 90})
        self.clock.value = 50
        self.overlay.check_stale()
        self.assertTrue(self.overlay.paused)
        self.clock.value = 100
        self.overlay.check_stale()
        self.assertFalse(self.overlay.paused)
        self.assertEqual(self.overlay.acknowledgement(), {'version': 1, 'shown': False})

    def test_server_relative_deadline_ignores_wall_clock_skew(self):
        self.overlay.wall_clock = lambda: 100_000
        self.overlay.update_pause({'paused': True, 'version': 1, 'until': 100, 'remaining': 60})
        self.assertTrue(self.overlay.paused)
        self.clock.value = 69.9
        self.overlay.check_stale()
        self.assertTrue(self.overlay.paused)
        self.clock.value = 70
        self.overlay.check_stale()
        self.assertFalse(self.overlay.paused)

    def test_repeated_snapshot_cannot_extend_or_resurrect_pause(self):
        state = {'paused': True, 'version': 2, 'until': 100, 'remaining': 60}
        self.overlay.update_pause(state)
        self.clock.value = 50
        self.overlay.update_pause(state)
        self.clock.value = 70
        self.overlay.check_stale()
        self.assertFalse(self.overlay.paused)
        self.overlay.update_pause(state)
        self.assertFalse(self.overlay.paused)
        self.overlay.update_pause({**state, 'version': 3})
        self.assertTrue(self.overlay.paused)

    def test_stale_release_does_not_unlock_newer_pause(self):
        self.overlay.update_pause({'paused': True, 'version': 2, 'until': 100})
        self.overlay.update_pause({'paused': False, 'version': 1})
        self.assertTrue(self.overlay.paused)

    def test_invalid_deadlines_do_not_acknowledge_or_replace_pause(self):
        self.overlay.update_pause({'paused': True, 'version': 1, 'until': 100})
        for value in (None, True, '90', float('nan'), float('inf'), 10**1000):
            for field in ('until', 'remaining'):
                with self.subTest(field=field, value=str(value)[:20]):
                    state = {'paused': True, 'version': 2, 'until': 100, 'remaining': 90, field: value}
                    self.overlay.update_pause(state)
                    self.assertEqual(self.overlay.acknowledgement(), {'version': 1, 'shown': True})

    def test_pause_is_bounded_to_two_hours(self):
        self.overlay.update_pause({'paused': True, 'version': 1, 'until': 100000, 'remaining': 100000})
        self.clock.value += 7200
        self.overlay.check_stale()
        self.assertFalse(self.overlay.paused)

    def test_closed_workshop_does_not_change_pause_version(self):
        self.overlay.update_workshop_closed(True)
        self.assertFalse(self.overlay.paused)
        self.overlay.update_pause({'paused': True, 'version': 2, 'message': 'Pause', 'until': 100})
        self.overlay.update_workshop_closed(False)
        self.assertTrue(self.overlay.paused)
        self.assertEqual(self.overlay.acknowledgement()['version'], 2)

    def test_offline_releases_server_pause_and_workshop_closure(self):
        self.overlay.update_pause({'paused': True, 'version': 2, 'until': 100})
        self.overlay.update_workshop_closed(True, '3D-værkstedet')
        self.overlay.offline()
        self.assertFalse(self.overlay.paused)
        self.assertFalse(self.overlay._closure)


if __name__ == '__main__':
    unittest.main()
