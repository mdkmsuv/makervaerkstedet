"""Transactional semantic tests for the makerspace configuration store."""

import copy
import tempfile
import threading
import unittest
import uuid

from makerspace_store import Conflict, MakerspaceStore
from makerspace_workflows import templates


SCOPES = ["makerspace:read", "machines:write", "workflows:write",
          "jobs:write", "audit:read"]


def actor(role="teacher", suffix="0"):
    return {"id": role + ":" + suffix * 32, "role": role, "scopes": list(SCOPES)}


class MakerspaceStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = self.temp.name + "/makerspace.sqlite3"
        self.store = MakerspaceStore(self.path)
        self.teacher = actor()

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def envelope(action, params, expected, request_id=None):
        return {"action": action, "params": params,
                "request_id": request_id or uuid.uuid4().hex,
                "expected_version": expected}

    def execute(self, action, params, expected, who=None, request_id=None):
        return self.store.execute(who or self.teacher,
                                  self.envelope(action, params, expected, request_id))

    def create_manual_workflow(self):
        definition = templates()["manual"]
        created = self.execute("workflow.create", {"definition": definition}, 0)
        workflow = created["result"]
        self.execute("workflow.publish", {"id": workflow["id"]}, 1)
        return workflow["id"]

    def create_machine(self, workflow_id, enabled=True, name="Bord 1"):
        return self.execute("machine.create", {
            "name": name, "location": "Lokale 1", "enabled": enabled,
            "workflow_id": workflow_id,
        }, 0)["result"]

    def test_manual_workflow_machine_job_revision_lock_and_rollback(self):
        workflow_id = self.create_manual_workflow()
        machine = self.create_machine(workflow_id)
        first_job = self.execute("job.create", {
            "machine_id": machine["id"], "title": "Første opgave",
        }, 0)["result"]
        self.assertEqual(first_job["workflow_revision"], 1)
        old_definition = copy.deepcopy(first_job["definition"])

        changed = copy.deepcopy(old_definition)
        changed["name"] = "Manualt værkstedsforløb v2"
        self.execute("workflow.edit", {"id": workflow_id, "definition": changed}, 2)
        self.execute("workflow.publish", {"id": workflow_id}, 3)
        current = next(w for w in self.store.snapshot()["workflows"] if w["id"] == workflow_id)
        self.assertEqual(current["active_revision"], 2)
        locked = next(j for j in self.store.snapshot()["jobs"] if j["id"] == first_job["id"])
        self.assertEqual(locked["workflow_revision"], 1)
        self.assertEqual(locked["definition"], old_definition)

        self.execute("workflow.rollback", {"id": workflow_id, "revision": 1}, 4)
        machine_again = next(m for m in self.store.snapshot()["machines"] if m["id"] == machine["id"])
        second_job = self.execute("job.create", {
            "machine_id": machine_again["id"], "title": "Efter rollback",
        }, 0)["result"]
        self.assertEqual(second_job["workflow_revision"], 1)

    def test_snapshot_marks_draft_changes_across_publish_and_rollback(self):
        workflow_id = self.create_manual_workflow()
        current = next(w for w in self.store.snapshot()['workflows'] if w['id'] == workflow_id)
        self.assertFalse(current['has_unpublished_changes'])
        changed = copy.deepcopy(current['draft'])
        changed['steps'][0]['label'] = 'Indsend ændret'
        self.execute('workflow.edit', {'id': workflow_id, 'definition': changed}, 2)
        edited = next(w for w in self.store.snapshot()['workflows'] if w['id'] == workflow_id)
        self.assertTrue(edited['has_unpublished_changes'])
        self.assertEqual(len(edited['active_steps']), len(current['active_steps']))
        self.execute('workflow.publish', {'id': workflow_id}, 3)
        published = next(w for w in self.store.snapshot()['workflows'] if w['id'] == workflow_id)
        self.assertFalse(published['has_unpublished_changes'])
        self.assertEqual(published['active_revision'], 2)
        self.execute('workflow.rollback', {'id': workflow_id, 'revision': 1}, 4)
        rolled_back = next(w for w in self.store.snapshot()['workflows'] if w['id'] == workflow_id)
        self.assertTrue(rolled_back['has_unpublished_changes'])
        self.assertEqual(rolled_back['active_revision'], 1)

    def test_stale_expected_version_does_not_change_object(self):
        workflow_id = self.create_manual_workflow()
        before = self.store.snapshot()
        with self.assertRaises(Conflict):
            self.execute("workflow.rollback", {"id": workflow_id, "revision": 1}, 1)
        after = self.store.snapshot()
        self.assertEqual(before, after)

    def test_idempotency_exact_response_and_changed_payload_conflict(self):
        request_id = uuid.uuid4().hex
        params = {"name": "Manuel", "location": "", "enabled": False, "workflow_id": None}
        first = self.execute("machine.create", params, 0, request_id=request_id)
        again = self.execute("machine.create", dict(params), 0, request_id=request_id)
        self.assertEqual(first, again)
        self.assertEqual(len(self.store.snapshot()["machines"]), 1)
        with self.assertRaises(Conflict):
            self.execute("machine.create", {**params, "name": "Anden"}, 0, request_id=request_id)
        self.assertEqual(len(self.store.snapshot()["machines"]), 1)

    def test_duplicate_station_name_and_location_conflict_without_overwrite(self):
        first = self.create_machine(None, name='Prototypebord')
        before = self.store.snapshot()
        with self.assertRaises(Conflict):
            self.create_machine(None, name=' prototypebord ')
        second = self.create_machine(None, name='Andet bord')
        with self.assertRaises(Conflict):
            self.execute('machine.update', {'id': second['id'], 'name': 'PROTOTYPEBORD',
                'location': 'lokale 1', 'enabled': True, 'workflow_id': None}, 1)
        snapshot = self.store.snapshot()
        self.assertEqual(len(snapshot['machines']), 2)
        self.assertIn(first, snapshot['machines'])
        self.assertIn(second, snapshot['machines'])
        self.assertEqual(before['machines'], [first])

    def test_scope_and_teacher_step_denial_are_audited_without_payload(self):
        read_only = actor()
        read_only['scopes'] = ['makerspace:read']
        with self.assertRaises(PermissionError):
            self.execute("machine.create", {
                "name": "Afvist", "location": "", "enabled": False, "workflow_id": None,
            }, 0, who=read_only)
        workflow_id = self.create_manual_workflow()
        machine = self.create_machine(workflow_id)
        job = self.execute("job.create", {"machine_id": machine["id"], "title": "Opgave"}, 0)["result"]
        with self.assertRaises(PermissionError):
            self.execute("job.advance", {
                "id": job["id"], "step_id": "prepare", "confirmed": True,
            }, 1, who=read_only)
        audit = self.store.audit()
        self.assertTrue(any(row["outcome"] == "denied" for row in audit))
        self.assertTrue(all("payload" not in row for row in audit))
        self.assertTrue(all("Opgave" not in str(row) for row in audit))

    def test_agent_actor_is_rejected_before_transaction(self):
        cached_actor = actor('agent', 'a')
        with self.assertRaises(PermissionError):
            self.execute('machine.create', {'name': 'Afvist', 'location': '', 'enabled': False,
                                            'workflow_id': None}, 0, who=cached_actor)
        self.assertEqual(self.store.snapshot()['machines'], [])

    def test_historical_agents_row_is_data_only(self):
        with self.store.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS agents (id TEXT PRIMARY KEY, name TEXT, scopes TEXT, token_hash TEXT, created_at REAL, revoked INTEGER)')
            db.execute('INSERT INTO agents VALUES (?,?,?,?,?,?)',
                       ('legacy', 'Historisk', '["makerspace:read"]', 'deadbeef', 1.0, 0))
        with self.assertRaises(PermissionError):
            self.execute('machine.create', {'name': 'Afvist', 'location': '', 'enabled': False,
                                            'workflow_id': None}, 0,
                         who={'id': 'agent:' + 'a' * 32, 'role': 'agent',
                              'scopes': list(SCOPES)})
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT token_hash FROM agents WHERE id=?', ('legacy',)).fetchone()[0], 'deadbeef')

    def test_database_reopen_preserves_objects_and_manual_disabled_needs_no_hardware(self):
        machine = self.create_machine(None, enabled=False, name="Midlertidig station")
        reopened = MakerspaceStore(self.path)
        result = next(m for m in reopened.snapshot()["machines"] if m["id"] == machine["id"])
        self.assertFalse(result["enabled"])
        self.assertIsNone(result["workflow_id"])
        self.assertEqual(result["kind"], "manual")
        self.assertEqual(result["adapter"], "manual")

    def test_two_store_instances_only_one_expected_version_update_wins(self):
        workflow_id = self.create_manual_workflow()
        machine = self.create_machine(workflow_id)
        first = MakerspaceStore(self.path)
        second = MakerspaceStore(self.path)
        barrier = threading.Barrier(2)
        results = []

        def update(store, name):
            try:
                barrier.wait(timeout=2)
                result = store.execute(self.teacher, self.envelope(
                    "machine.update", {"id": machine["id"], "name": name,
                                        "location": "Lokale 1", "enabled": True,
                                        "workflow_id": workflow_id}, 1))
                results.append(("ok", result["result"]["name"]))
            except Conflict:
                results.append(("conflict", name))

        threads = [threading.Thread(target=update, args=(first, "Vinder A")),
                   threading.Thread(target=update, args=(second, "Vinder B"))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(sorted(item[0] for item in results), ["conflict", "ok"])
        final = next(m for m in self.store.snapshot()["machines"] if m["id"] == machine["id"])
        self.assertIn(final["name"], ("Vinder A", "Vinder B"))


if __name__ == "__main__":
    unittest.main()
