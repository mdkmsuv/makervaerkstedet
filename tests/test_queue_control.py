import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock
import uuid

from app.core import Queue
from app.printer_bridge import PrinterBridge
from app.printer_config import ConfigConflict


ACTOR = 'teacher:' + 'a' * 32


class QueueControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.queue = Queue(self.root, [])
        self.addCleanup(self._close)

    def _close(self):
        if getattr(self, 'queue', None) is not None:
            self.queue.close()
            self.queue = None
        self.temp.cleanup()

    @staticmethod
    def envelope(request_id=None, paused=True, expected=1):
        return {'action': 'printer.queue.pause',
                'params': {'id': 'print', 'paused': paused},
                'expected_version': expected,
                'request_id': request_id or uuid.uuid4().hex}

    def test_pause_receipt_audit_and_snapshot_share_version(self):
        request = self.envelope()
        result = self.queue.control_queue(ACTOR, request)
        self.assertEqual(result['result'], {'id': 'print', 'paused': True, 'version': 2})
        self.assertTrue(self.queue.paused)
        self.assertEqual(self.queue.queue_control_version, 2)
        row = self.queue.db.execute("SELECT value FROM settings WHERE key='queue_control_version'").fetchone()
        self.assertEqual(row[0], '2')
        audit = self.queue.db.execute('SELECT job_id,step_id FROM workflow_audit').fetchone()
        self.assertEqual(tuple(audit), ('queue:print', 'pause'))

    def test_replay_returns_old_receipt_after_opposite_change(self):
        request = self.envelope(paused=True, expected=1)
        first = self.queue.control_queue(ACTOR, request)
        self.queue.control_queue(ACTOR, self.envelope(paused=False, expected=2))
        replay = self.queue.control_queue(ACTOR, request)
        self.assertEqual(replay, first)
        self.assertFalse(self.queue.paused)
        self.assertEqual(self.queue.queue_control_version, 3)

    def test_legacy_pause_bumps_version_and_stales_agent(self):
        self.queue.pause(True)
        self.assertEqual(self.queue.queue_control_version, 2)
        with self.assertRaises(ConfigConflict):
            self.queue.control_queue(ACTOR, self.envelope(expected=1))

    def test_restart_preserves_pause_and_version(self):
        self.queue.control_queue(ACTOR, self.envelope())
        self.queue.close()
        self.queue = Queue(self.root, [])
        self.assertTrue(self.queue.paused)
        self.assertEqual(self.queue.queue_control_version, 2)

    def test_database_error_rolls_back_pause_version_receipt_and_audit(self):
        request = self.envelope()
        trigger = 'fail_queue_control'
        self.queue.db.execute(f'''CREATE TRIGGER {trigger} BEFORE INSERT ON workflow_commands
            WHEN NEW.request_id = '{request['request_id']}'
            BEGIN SELECT RAISE(ABORT, 'injected'); END''')
        self.queue.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.queue.control_queue(ACTOR, request)
        self.assertFalse(self.queue.paused)
        self.assertEqual(self.queue.queue_control_version, 1)
        self.assertIsNone(self.queue.db.execute("SELECT value FROM settings WHERE key='paused'").fetchone())
        self.assertEqual(self.queue.db.execute("SELECT value FROM settings WHERE key='queue_control_version'").fetchone()[0], '1')
        self.assertIsNone(self.queue.db.execute('SELECT 1 FROM workflow_commands WHERE request_id=?', (request['request_id'],)).fetchone())
        self.assertEqual(self.queue.db.execute('SELECT count(*) FROM workflow_audit').fetchone()[0], 0)

    def test_legacy_pause_rolls_back_memory_on_settings_error(self):
        self.queue.db.execute('''CREATE TRIGGER fail_legacy_pause BEFORE INSERT ON settings
            WHEN NEW.key='paused' BEGIN SELECT RAISE(ABORT, 'injected'); END''')
        self.queue.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.queue.pause(True)
        self.assertFalse(self.queue.paused)
        self.assertEqual(self.queue.queue_control_version, 1)
        self.assertEqual(self.queue.db.execute("SELECT value FROM settings WHERE key='queue_control_version'").fetchone()[0], '1')

    def test_pause_does_not_change_active_job_or_bed_state_and_blocks_reserve(self):
        self.queue.configs = {'p': {'id': 'p', 'name': 'P1S', 'enabled': True}}
        self.queue.printers = {'p': {'id': 'p', 'name': 'P1S', 'state': 'IDLE', 'seen': 0,
                                     'state_seen': 0, 'tray_seen': 0, 'bed_clear': True,
                                     'trays': [], 'error': 0, 'task': '', 'enabled': True}}
        self.queue.db.execute('''INSERT INTO jobs
            (id,request_id,digest,filename,meta,created,updated,status,printer_id,workflow,
             workflow_step,workflow_version,workflow_authorized,workflow_complete)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            ('b' * 32, 'active-request', 'digest', 'active.svg', '{}', 1, 1,
             'queued', 'p', '{"id":"w","revision":1,"definition":{"name":"W","kind":"3d_print","steps":[]}}',
             0, 1, 1, 0))
        self.queue.db.commit()
        before = dict(self.queue.get_job('b' * 32))
        self.queue.control_queue(ACTOR, self.envelope(paused=True))
        self.assertIsNone(self.queue.reserve())
        self.queue.control_queue(ACTOR, self.envelope(paused=False, expected=2))
        after = dict(self.queue.get_job('b' * 32))
        self.assertEqual(after, before)
        self.assertTrue(self.queue.printers['p']['bed_clear'])

    def test_queue_and_printer_receipts_share_request_namespace(self):
        app = SimpleNamespace(folder=self.root, settings={'printers': []},
                              settings_path=self.root / 'settings.json', settings_lock=__import__('threading').RLock(),
                              queue=self.queue, demo=False, drivers={}, slicer=SimpleNamespace(profiles={}))
        bridge = PrinterBridge(app, lambda *_: None)
        request_id = uuid.uuid4().hex
        queue_request = self.envelope(request_id=request_id)
        bridge.execute(ACTOR, queue_request)
        printer_request = {'action': 'printer.create', 'params': {
            'name': 'P1S', 'model': 'P1S', 'host': '127.0.0.1', 'access_code': 'x',
            'location': 'værksted', 'serial': 's', 'enabled': False,
            'bed_type': 'Textured PEI Plate'}, 'expected_version': 0,
                           'request_id': request_id}
        with self.assertRaises(ConfigConflict):
            bridge.execute(ACTOR, printer_request)

    def test_bridge_snapshot_reports_queue_control(self):
        app = SimpleNamespace(folder=self.root, settings={'printers': []},
                              settings_path=self.root / 'settings.json', settings_lock=__import__('threading').RLock(),
                              queue=self.queue, demo=False, drivers={}, slicer=SimpleNamespace(profiles={}))
        bridge = PrinterBridge(app, lambda *_: None)
        self.assertEqual(bridge.snapshot()['queue'], {'id': 'print', 'paused': False, 'version': 1})


if __name__ == '__main__':
    unittest.main()
