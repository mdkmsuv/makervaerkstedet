import json
from pathlib import Path
import tempfile
import unittest
import sys
sys.path.insert(0, str(Path(__file__).parents[1]))
from benchmark import cleanup_fixture, device_fixture, memory_recommendation, quantiles


class BenchmarkTest(unittest.TestCase):
    def test_quantiles_and_empty(self):
        self.assertEqual(quantiles([])['count'], 0)
        result = quantiles([.001, .002, .010, .020])
        self.assertEqual(result['count'], 4)
        self.assertGreaterEqual(result['p95_ms'], result['p50_ms'])

    def test_memory_recommendation_uses_peak_and_minimum(self):
        self.assertEqual(memory_recommendation([{'ram_used': 1 * 1024 ** 3}]), 4)
        self.assertEqual(memory_recommendation([{'ram_used': 5 * 1024 ** 3}]), 7)
        self.assertIsNone(memory_recommendation([]))

    def test_fixture_cleanup_preserves_other_devices_and_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp) / 'enheder.json'
            registry.write_text(json.dumps({'enheder': {'existing': {'token_sha256': 'a' * 64, 'ui_token_sha256': 'b' * 64}}}))
            names, tokens, entries = device_fixture(registry, 24)
            self.assertEqual(len(names), 24)
            data = json.loads(registry.read_text())
            for name in names:
                self.assertEqual(data['enheder'][name]['token_sha256'], __import__('hashlib').sha256(tokens[name][0].encode()).hexdigest())
            # A modified entry must survive cleanup; only our original hashes
            # may be removed.
            changed = next(iter(names))
            data['enheder'][changed]['token_sha256'] = 'c' * 64
            registry.write_text(json.dumps(data))
            cleanup_fixture(registry, names, entries)
            data = json.loads(registry.read_text())
            self.assertEqual(set(data['enheder']), {'existing', changed})


if __name__ == '__main__': unittest.main()
