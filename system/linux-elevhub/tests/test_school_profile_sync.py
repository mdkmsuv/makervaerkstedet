"""Native, isolated root checks for the authenticated school profile channel."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'administration'))
import school_profile_sync as M


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class Transport:
    def __init__(self, revision=1, name='Testskole', assets=True):
        self.calls = []
        self.files = {}
        manifest = {'schemaVersion': 1, 'school_name': name, 'theme': 'green', 'font': 'manrope'}
        style = {}
        descriptors = []
        if assets:
            for key, suffix, raw in [('logo', '.png', b'\x89PNG\r\n\x1a\nsynthetic'),
                                     ('website_css', '.css', b'body{color:#345678}'),
                                     ('font', '.woff2', b'synthetic-font')]:
                digest = sha(raw)
                filename = 'managed-' + digest + suffix
                endpoint = M.API + '/assets/' + digest + suffix
                self.files[endpoint] = raw
                descriptors.append({'name': filename, 'sha256': digest, 'size': len(raw), 'path': endpoint})
                if key == 'font':
                    style['static'] = {'/fonts/example.woff2': {'path': filename, 'sha256': digest}}
                else:
                    style[key] = filename
                    style[key + '_sha256'] = digest
        manifest['style'] = style
        self.offer = {'schema': 1, 'enabled': True, 'revision': revision,
                      'manifest': {'value': manifest, 'size': len(canonical(manifest)), 'sha256': sha(canonical(manifest))},
                      'assets': descriptors}

    def get(self, path, limit):
        self.calls.append((path, limit))
        return canonical(self.offer) if path == M.API else self.files[path]


@unittest.skipUnless(hasattr(os, 'geteuid') and os.geteuid() == 0, 'native Linux root checks')
class SchoolProfileSync(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mkv-profile-', dir='/tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / 'etc/elevhub/local-appearance'
        self.manifest = self.base / 'manifest.json'
        self.ledger = self.root / 'var/lib/makervaerkstedet-updater/school-profile.json'
        self.activations = []

    def sync(self, client):
        return M.sync(client, root=self.root, activation=lambda root: self.activations.append(root))

    def install(self):
        client = Transport()
        result = self.sync(client)
        self.assertEqual(result, {'changed': True, 'revision': 1, 'code': None})
        return client

    def test_delivers_png_css_font_metadata_and_is_idempotent(self):
        client = self.install()
        self.assertEqual(self.manifest.read_bytes(), canonical(client.offer['manifest']['value']))
        for item in client.offer['assets']:
            self.assertEqual((self.base / item['name']).read_bytes(), client.files[item['path']])
        self.assertEqual(self.activations, [self.root])
        client.calls.clear()
        self.assertEqual(self.sync(client), {'changed': False, 'revision': 1, 'code': None})
        self.assertEqual(self.activations, [self.root])
        self.assertEqual(client.calls, [(M.API, M.MAX_METADATA)])

    def test_disabled_keeps_local_profile_and_highwater(self):
        self.install()
        before = self.manifest.read_bytes()
        ledger = self.ledger.read_bytes()
        client = Transport()
        client.offer = {'schema': 1, 'enabled': False}
        self.assertEqual(self.sync(client)['code'], 'school_profile_disabled')
        self.assertEqual(self.manifest.read_bytes(), before)
        self.assertEqual(self.ledger.read_bytes(), ledger)
        self.assertEqual(len(self.activations), 1)

    def test_integrity_or_invalid_reader_input_never_changes_profile(self):
        self.install()
        before = self.manifest.read_bytes()
        client = Transport(2, 'Ny skole')
        client.files[client.offer['assets'][0]['path']] = b'tampered'
        self.assertEqual(self.sync(client)['code'], 'school_profile_asset_integrity')
        self.assertEqual(self.manifest.read_bytes(), before)
        self.assertEqual(self.sync(Transport(1))['code'], 'school_profile_stale')
        bad = Transport(3, 'Ny skole', assets=False)
        bad.offer['manifest']['value']['theme'] = 'invalid'
        value = canonical(bad.offer['manifest']['value'])
        bad.offer['manifest'].update(size=len(value), sha256=sha(value))
        self.assertEqual(self.sync(bad)['code'], 'school_profile_manifest_invalid')
        self.assertEqual(self.manifest.read_bytes(), before)

    def test_stale_revision_and_same_revision_changed_hash_rejected(self):
        self.install()
        self.assertEqual(self.sync(Transport(0))['code'], 'school_profile_metadata_invalid')
        self.assertEqual(self.sync(Transport(1, 'Changed'))['code'], 'school_profile_revision_changed')
        self.assertIsNone(self.sync(Transport(3))['code'])
        self.assertEqual(self.sync(Transport(2))['code'], 'school_profile_stale')

    def test_operator_manifest_and_asset_edits_are_preserved(self):
        client = self.install()
        self.manifest.write_bytes(b'{"operator":true}')
        self.assertEqual(self.sync(Transport(2))['code'], 'school_profile_conflict')
        self.assertEqual(self.manifest.read_bytes(), b'{"operator":true}')
        self.manifest.write_bytes(canonical(client.offer['manifest']['value']))
        asset = self.base / client.offer['assets'][0]['name']
        asset.write_bytes(b'operator asset')
        self.assertEqual(self.sync(Transport(2))['code'], 'school_profile_asset_conflict')
        self.assertEqual(asset.read_bytes(), b'operator asset')

    def test_initial_managed_save_replaces_manifest_but_preserves_unrelated_files(self):
        self.base.mkdir(parents=True)
        self.manifest.write_bytes(b'{"legacy":true}')
        legacy = self.base / 'legacy.png'
        legacy.write_bytes(b'old logo')
        self.install()
        self.assertEqual(legacy.read_bytes(), b'old logo')

    def test_crash_before_manifest_safe_retry_and_after_manifest_finalize(self):
        client = self.install()
        before = self.manifest.read_bytes()
        newer = Transport(2, 'Ny skole')
        original = M.atomic
        def interrupt_before(path, raw, mode):
            if path == self.manifest:
                raise KeyboardInterrupt('power loss')
            return original(path, raw, mode)
        with patch.object(M, 'atomic', side_effect=interrupt_before):
            with self.assertRaises(KeyboardInterrupt):
                self.sync(newer)
        self.assertEqual(self.manifest.read_bytes(), before)
        self.assertTrue(self.sync(newer)['changed'])
        newer = Transport(3, 'Tredje skole')
        def interrupt_after(path, raw, mode):
            result = original(path, raw, mode)
            if path == self.manifest:
                raise KeyboardInterrupt('power loss')
            return result
        with patch.object(M, 'atomic', side_effect=interrupt_after):
            with self.assertRaises(KeyboardInterrupt):
                self.sync(newer)
        self.assertEqual(self.manifest.read_bytes(), canonical(newer.offer['manifest']['value']))
        newer.get = lambda *_: (_ for _ in ()).throw(OSError('network unavailable'))
        self.assertEqual(self.sync(newer), {'changed': True, 'revision': 3, 'code': None})
        self.assertIsNone(json.loads(self.ledger.read_bytes())['pending'])

    def test_crash_then_operator_edit_conflicts_and_preserves(self):
        self.install()
        original = M.atomic
        def interrupt(path, raw, mode):
            if path == self.manifest:
                raise KeyboardInterrupt()
            return original(path, raw, mode)
        with patch.object(M, 'atomic', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.sync(Transport(2, 'Ny skole'))
        self.manifest.write_bytes(b'operator')
        self.assertEqual(self.sync(Transport(2))['code'], 'school_profile_conflict')
        self.assertEqual(self.manifest.read_bytes(), b'operator')

    def test_no_extra_assets_duplicate_alias_or_external_asset_endpoint(self):
        for mutation in ('extra', 'duplicate', 'url', 'traversal'):
            with self.subTest(mutation=mutation):
                client = Transport()
                item = dict(client.offer['assets'][0])
                if mutation == 'extra':
                    item['name'] = 'unused.png'
                    client.offer['assets'].append(item)
                elif mutation == 'duplicate':
                    client.offer['assets'].append(item)
                elif mutation == 'url':
                    client.offer['assets'][0]['path'] = 'https://external.test/logo.png'
                else:
                    client.offer['assets'][0]['name'] = '../logo.png'
                self.assertEqual(self.sync(client)['code'], 'school_profile_metadata_invalid')
                self.assertFalse(self.manifest.exists())

    def test_symlink_or_unprotected_parent_rejected_without_external_write(self):
        external = self.root / 'external'
        external.mkdir()
        (self.root / 'etc/elevhub').mkdir(parents=True)
        self.base.symlink_to(external, target_is_directory=True)
        self.assertEqual(self.sync(Transport())['code'], 'linked_path')
        self.assertEqual(list(external.iterdir()), [])

    def test_hardlinked_profile_rejected_without_writing_linked_file(self):
        self.base.mkdir(parents=True)
        outside = self.root / 'operator.json'
        outside.write_bytes(b'operator')
        os.link(outside, self.manifest)
        self.assertEqual(self.sync(Transport())['code'], 'school_profile_local_file_invalid')
        self.assertEqual(outside.read_bytes(), b'operator')

    def test_unprotected_parent_rejected(self):
        parent = self.root / 'etc'
        parent.mkdir(mode=0o777)
        parent.chmod(0o777)
        self.assertEqual(self.sync(Transport())['code'], 'unprotected_configuration')
        self.assertFalse(self.manifest.exists())

    def test_metadata_bounds_reject_before_asset_downloads(self):
        client = Transport()
        client.offer['assets'][0]['size'] = M.MAX_ASSET + 1
        self.assertEqual(self.sync(client)['code'], 'school_profile_metadata_invalid')
        self.assertEqual(len(client.calls), 1)
        self.assertFalse(self.manifest.exists())
        client = Transport()
        client.offer['assets'] *= M.MAX_ASSETS
        self.assertEqual(self.sync(client)['code'], 'school_profile_metadata_invalid')
        self.assertEqual(len(client.calls), 1)
        self.assertFalse(self.manifest.exists())

    def test_activation_failure_retries_without_republishing(self):
        client = Transport()
        result = M.sync(client, root=self.root, activation=lambda _: (_ for _ in ()).throw(RuntimeError('render failed')))
        self.assertEqual(result['code'], 'school_profile_activation_failed')
        self.assertTrue(result['changed'])
        before = self.manifest.stat().st_mtime_ns
        self.assertTrue(self.sync(client)['changed'])
        self.assertEqual(self.manifest.stat().st_mtime_ns, before)

    def test_bounded_json_rejects_duplicates_and_transport_failures(self):
        client = Transport()
        client.get = lambda *_: b'{"schema":1,"schema":1,"enabled":false}'
        self.assertEqual(self.sync(client)['code'], 'school_profile_metadata_invalid')
        client.get = lambda *_: b'x' * (M.MAX_METADATA + 1)
        self.assertEqual(self.sync(client)['code'], 'school_profile_metadata_invalid')
        client.get = lambda *_: (_ for _ in ()).throw(OSError('network'))
        self.assertEqual(self.sync(client)['code'], 'school_profile_sync_failed')


if __name__ == '__main__':
    unittest.main()
