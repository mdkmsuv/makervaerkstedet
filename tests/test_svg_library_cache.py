"""Synthetic network boundary, real parser/sanitizer and persistent cache."""
import hashlib
import json
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import svg_library as lib


HTML = b'<article class="card" data-url="Animals/cat.svg" data-title="Cat" data-category="Animals">'
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 20"><path d="M0 0L10 20"/></svg>'
PATH = '/svg-bibliotek/Animals/cat.svg'
ITEM_ID = hashlib.sha256(PATH.encode()).hexdigest()


def online(path):
    return {'/svg-bibliotek/': HTML, PATH: SVG}[path]


def offline(path):
    raise OSError('synthetic offline')


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name) / 'cache'

    def library(self, read=online):
        value = lib.Library(cache_dir=self.cache)
        value.read = read
        return value

    def test_online_then_offline_restart_preserves_catalogue_and_sanitized_svg(self):
        first = self.library()
        self.assertEqual(first.catalogue(), [{'id': ITEM_ID, 'title': 'Cat', 'category': 'Animals'}])
        svg = first.svg(ITEM_ID)
        self.assertIn('height="80mm"', svg)
        self.assertEqual(first.status()['cache_state'], 'ready')
        version = first.status()['version']
        second = self.library(offline)
        self.assertEqual(second.status()['cache_state'], 'stale')
        self.assertEqual(second.catalogue(), first.catalogue())
        self.assertEqual(second.svg(ITEM_ID), svg)
        self.assertEqual(second.status()['version'], version)
        self.assertTrue(second.status()['last_success'])
        self.assertEqual(second.status()['source_url'], lib.ORIGIN + '/svg-bibliotek/')

    def test_status_is_network_free_and_empty_library_stays_supported(self):
        library = self.library(lambda path: self.fail('status performed network I/O'))
        self.assertEqual(library.status()['cache_state'], 'empty')
        self.assertTrue(library.status()['enabled'])
        self.assertEqual(library.status()['version'], 0)

    def test_offline_uncached_file_is_an_error(self):
        self.library().catalogue()
        library = self.library(offline)
        with self.assertRaises((ValueError, OSError)):
            library.svg(ITEM_ID)
        self.assertEqual(library.status()['cache_state'], 'stale')

    def test_offline_catalogue_failure_serves_cached_file_without_second_network_wait(self):
        self.library().svg(ITEM_ID)
        def disconnected(path):
            if path != '/svg-bibliotek/':
                self.fail('offline cache incurred a second network wait')
            raise OSError('offline')
        self.assertIn('height="80mm"', self.library(disconnected).svg(ITEM_ID))

    def test_offline_retry_backoff_does_not_block_every_cached_file(self):
        self.library().svg(ITEM_ID)
        attempts = []
        def disconnected(path):
            attempts.append(path)
            if len(attempts) > 1:
                self.fail('offline cache repeated a network wait during backoff')
            raise OSError('offline')
        library = self.library(disconnected)
        self.assertIn('height="80mm"', library.svg(ITEM_ID))
        self.assertIn('height="80mm"', library.svg(ITEM_ID))

    def test_corrupt_catalogue_never_becomes_offline_content(self):
        self.library().catalogue()
        (self.cache / 'catalogue.json').write_text('{broken', encoding='utf-8')
        library = self.library(offline)
        self.assertEqual(library.status()['cache_state'], 'empty')
        with self.assertRaises((ValueError, OSError)):
            library.catalogue()

    def test_corrupt_cached_svg_is_rejected_without_losing_catalogue(self):
        library = self.library()
        library.svg(ITEM_ID)
        (self.cache / (ITEM_ID + '.json')).write_text('{broken', encoding='utf-8')
        library = self.library(offline)
        self.assertEqual(len(library.catalogue()), 1)
        with self.assertRaises((ValueError, OSError)):
            library.svg(ITEM_ID)

    def test_valid_json_tampering_fails_cache_integrity_check(self):
        self.library().svg(ITEM_ID)
        for filename, field in [('catalogue.json', 'last_success'), (ITEM_ID + '.json', 'svg')]:
            path = self.cache / filename
            original = path.read_bytes()
            data = json.loads(original)
            data['data'][field] += 'tampered'
            path.write_text(json.dumps(data), encoding='utf-8')
            library = self.library(offline)
            if filename == 'catalogue.json':
                self.assertEqual(library.status()['cache_state'], 'empty')
            else:
                with self.assertRaises(ValueError):
                    library.svg(ITEM_ID)
            path.write_bytes(original)

    def test_cache_manifest_cannot_introduce_arbitrary_paths(self):
        self.library().catalogue()
        path = self.cache / 'catalogue.json'
        envelope = json.loads(path.read_bytes())
        envelope['data']['paths'][ITEM_ID] = '/svg-bibliotek/../private.svg'
        raw = json.dumps(envelope['data'], ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()
        envelope['sha256'] = hashlib.sha256(raw).hexdigest()
        path.write_text(json.dumps(envelope), encoding='utf-8')
        self.assertEqual(self.library(offline).status()['cache_state'], 'empty')

    def test_linked_cache_directory_is_refused(self):
        target = Path(self.temp.name) / 'outside'
        target.mkdir()
        try:
            self.cache.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest('symlinks unavailable')
        with self.assertRaises((ValueError, OSError)):
            self.library()
        self.assertEqual(list(target.iterdir()), [])

    def test_linked_catalogue_and_file_are_not_read_or_overwritten(self):
        library = self.library()
        library.svg(ITEM_ID)
        outside = Path(self.temp.name) / 'outside.json'
        outside.write_text('private sentinel', encoding='utf-8')
        for filename in ('catalogue.json', ITEM_ID + '.json'):
            destination = self.cache / filename
            original = destination.read_bytes()
            destination.unlink()
            try:
                destination.symlink_to(outside)
            except OSError:
                self.skipTest('symlinks unavailable')
            with self.assertRaises((ValueError, OSError)):
                candidate = self.library(offline)
                candidate.svg(ITEM_ID)
            self.assertEqual(outside.read_text(encoding='utf-8'), 'private sentinel')
            destination.unlink()
            destination.write_bytes(original)

    def test_unknown_ids_and_path_traversal_are_rejected(self):
        library = self.library()
        for item_id in ('../secret', 'a' * 64, 'A' * 64):
            with self.subTest(item_id=item_id), self.assertRaises(ValueError):
                library.svg(item_id)
        for path in ('//evil.test/x', '/../secret', '/a\\b', 'https://evil.test/x'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                lib.Library(cache_dir=self.cache).read(path)

    def test_parser_keeps_ids_and_rejects_nonrelative_sources(self):
        parser = lib.Catalogue()
        parser.feed(HTML.decode() + ''.join('<article class="card" data-url="' + path + '">' for path in ('../x.svg', '/x.svg', 'https://evil.test/x.svg', 'x%20y.svg', 'x.svg?x=1')))
        self.assertEqual(parser.paths, {ITEM_ID: PATH})

    def test_active_or_oversized_svg_never_reaches_cache(self):
        for raw in (SVG.replace(b'<path', b'<script'), b'x' * (lib.MAX_BYTES + 1)):
            with self.subTest(size=len(raw)), self.assertRaises(ValueError):
                self.library(lambda path: HTML if path == '/svg-bibliotek/' else raw).svg(ITEM_ID)
        self.assertFalse((self.cache / (ITEM_ID + '.json')).exists())

    def test_sanitized_expansion_cannot_exceed_svg_bound(self):
        raw = SVG.replace(b'<path d="M0 0L10 20"/>', b'<title>' + b'>' * (lib.MAX_BYTES // 2) + b'</title>')
        self.assertLess(len(raw), lib.MAX_BYTES)
        with self.assertRaises(ValueError):
            lib.safe_svg(raw)

    def test_redirects_and_oversized_responses_are_rejected(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def geturl(self): return lib.ORIGIN + '/svg-bibliotek/'
            def read(self, size): return b'x' * size
        library = lib.Library(cache_dir=self.cache)
        with patch.object(library.opener, 'open', return_value=Response()):
            with self.assertRaises(ValueError):
                library.read('/svg-bibliotek/')
            with self.assertRaises(ValueError):
                library.read(PATH)
        self.assertIsNone(lib.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.test/'))

    def test_tls_keeps_hostname_and_certificate_verification(self):
        contexts = []
        original = ssl.create_default_context
        def context():
            result = original()
            contexts.append(result)
            return result
        with patch.object(lib.ssl, 'create_default_context', side_effect=context):
            self.library()
        self.assertEqual(contexts[0].verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(contexts[0].check_hostname)

    def test_cache_budget_evicts_old_files_and_keeps_catalogue(self):
        # Removing eviction would leave two individually valid files over budget.
        html = HTML + HTML.replace(b'cat.svg', b'dog.svg')
        library = self.library(lambda path: html if path == '/svg-bibliotek/' else SVG)
        with patch.object(lib, 'CACHE_BYTES', 400):
            items = library.catalogue()
            library.svg(items[0]['id'])
            library.svg(items[1]['id'])
        self.assertLessEqual(sum(path.stat().st_size for path in self.cache.glob('*.json') if path.name != 'catalogue.json'), 400)
        self.assertFalse((self.cache / (items[0]['id'] + '.json')).exists())
        self.assertTrue((self.cache / 'catalogue.json').is_file())


if __name__ == '__main__':
    unittest.main()
