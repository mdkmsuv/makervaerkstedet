import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pause import MAKS_PAUSE_SEKUNDER, PauseStore


class Clock:
    def __init__(self, value=1000.0):
        self.value = value

    def __call__(self):
        return self.value


class PauseStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.store = PauseStore(Path(self.temp.name), clock=self.clock)

    def tearDown(self):
        self.temp.cleanup()

    def test_pause_has_bounded_lease_and_public_message(self):
        state = self.store.set(True, message='Gem arbejdet først', expires_in=999999)
        self.assertTrue(state['paused'])
        self.assertEqual(state['message'], 'Gem arbejdet først')
        self.assertEqual(state['until'], 1000 + MAKS_PAUSE_SEKUNDER)
        self.assertEqual(state['remaining'], MAKS_PAUSE_SEKUNDER)

    def test_acknowledgement_requires_current_version(self):
        state = self.store.set(True, message='Pause')
        self.assertFalse(self.store.acknowledge('vaerksted-01', state['version'] - 1, True))
        self.assertTrue(self.store.acknowledge('vaerksted-01', state['version'], True))
        self.assertEqual(self.store.acknowledgement('vaerksted-01')['shown'], True)
        resumed = self.store.set(False)
        self.assertFalse(self.store.acknowledge('vaerksted-01', state['version'], True))
        self.assertTrue(self.store.acknowledge('vaerksted-01', resumed['version'], False))

    def test_expiry_increments_version_and_clears_pause(self):
        state = self.store.set(True, expires_in=10)
        self.clock.value = 1010
        expired = self.store.state()
        self.assertFalse(expired['paused'])
        self.assertEqual(expired['version'], state['version'] + 1)
        self.assertEqual(expired['message'], '')
        self.assertIsNone(expired['until'])

    def test_persisted_pause_survives_restart(self):
        state = self.store.set(True, message='Lærer taler')
        reloaded = PauseStore(Path(self.temp.name), clock=self.clock)
        self.assertEqual(reloaded.state()['version'], state['version'])
        self.assertEqual(reloaded.state()['message'], 'Lærer taler')

    def test_missing_until_cannot_create_permanent_pause(self):
        (Path(self.temp.name) / 'pause.json').write_text(
            '{"paused": true, "version": 8, "message": "gammel"}', encoding='utf-8')
        reloaded = PauseStore(Path(self.temp.name), clock=self.clock)
        self.assertFalse(reloaded.state()['paused'])

    def test_failed_disk_write_does_not_publish_uncommitted_pause(self):
        before = self.store.state()
        with patch.object(self.store, '_write_locked', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.store.set(True, message='Må ikke publiceres')
        self.assertEqual(self.store.state(), before)
        self.assertFalse(self.store.path.exists())
        committed = self.store.set(True, message='Efter genforsøg', expires_in=20)
        self.assertTrue(committed['paused'])
        self.assertEqual(self.store.state()['message'], 'Efter genforsøg')


if __name__ == '__main__':
    unittest.main()
