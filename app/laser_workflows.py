"""Durable, hardware-free workflow binding for the central laser inbox."""

from copy import deepcopy
from contextlib import contextmanager
import hashlib
import json
import re
import sqlite3
import time
from datetime import datetime, timezone

from .hardware_workflows import _definition
from .laser_inbox_lifecycle import cleanup_terminal_payload
from .printer_config import ConfigConflict

_ID = re.compile(r"^[a-f0-9]{32}$", re.ASCII)
_STATION = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", re.ASCII)
_ACTOR = re.compile(r"^teacher:[a-f0-9]{32}$", re.ASCII)
_MODELS = frozenset(("fbb2", "fhexa1"))


def _text(value, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(not c.isprintable() for c in value):
        raise ValueError("Ugyldig tekst.")
    return value.strip()


def _location(value):
    if not isinstance(value, str) or len(value) > 80 or any(not c.isprintable() for c in value):
        raise ValueError("Ugyldig tekst.")
    return value.strip()


def _job_id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("Ugyldigt laserjob-id.")
    return value


def _station_id(value):
    if not isinstance(value, str) or len(value) > 40 or not _STATION.fullmatch(value):
        raise ValueError("Ugyldigt station-id.")
    return value


def _binding(value):
    if not isinstance(value, dict) or set(value) != {"id", "revision", "definition"}:
        raise ValueError("Ugyldig workflowbinding.")
    if not isinstance(value["id"], str) or not _ID.fullmatch(value["id"]):
        raise ValueError("Ugyldigt workflow-id.")
    if type(value["revision"]) is not int or value["revision"] < 1:
        raise ValueError("Workflowrevisionen skal være positiv.")
    definition = _definition(value["definition"])
    if definition["kind"] != "laser":
        raise ValueError("Laserstationen kræver et laserworkflow.")
    return {"id": value["id"], "revision": value["revision"], "definition": deepcopy(definition)}


class LaserWorkflows:
    def __init__(self, inbox):
        self.inbox = inbox

    @contextmanager
    def _db(self):
        with self.inbox.db() as db:
            db.row_factory = sqlite3.Row
            yield db

    def initialize(self):
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            for name, declaration in (
                ("workflow", "TEXT"), ("workflow_step", "INTEGER NOT NULL DEFAULT 0"),
                ("workflow_version", "INTEGER NOT NULL DEFAULT 0"),
                ("workflow_authorized", "INTEGER NOT NULL DEFAULT 0"),
                ("physical_done", "INTEGER NOT NULL DEFAULT 0"),
                ("handoff_claimed", "INTEGER NOT NULL DEFAULT 0"),
                ("station_id", "TEXT"), ("workflow_model", "TEXT"),
                ("workflow_complete", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in columns:
                    db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}")
            db.execute("""CREATE TABLE IF NOT EXISTS laser_stations (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, location TEXT NOT NULL,
                model TEXT NOT NULL, enabled INTEGER NOT NULL, version INTEGER NOT NULL,
                workflow TEXT)""")
            db.execute("""CREATE TABLE IF NOT EXISTS laser_workflow_commands (
                actor TEXT NOT NULL, request_id TEXT NOT NULL, digest TEXT NOT NULL,
                response TEXT NOT NULL, PRIMARY KEY(actor, request_id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS laser_workflow_audit (
                at REAL NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
                target TEXT, request_id TEXT NOT NULL, outcome TEXT NOT NULL)""")

    def _actor(self, actor):
        actor_id = actor.get("id") if isinstance(actor, dict) else actor
        if not isinstance(actor_id, str) or not _ACTOR.fullmatch(actor_id):
            raise PermissionError("Ugyldig aktør.")
        return actor_id

    @staticmethod
    def _teacher(actor):
        return actor.startswith("teacher:")

    @staticmethod
    def _digest(envelope):
        return hashlib.sha256(json.dumps(envelope, sort_keys=True, ensure_ascii=False,
                                         separators=(",", ":"), allow_nan=False).encode()).hexdigest()

    def _public_station(self, row):
        binding = json.loads(row["workflow"]) if row["workflow"] else None
        workflow = None if binding is None else {"id": binding["id"], "revision": binding["revision"], "name": binding["definition"]["name"]}
        return {"id": row["id"], "name": row["name"], "location": row["location"],
                "model": row["model"], "enabled": bool(row["enabled"]),
                "version": row["version"], "workflow": workflow}

    @staticmethod
    def _public_job(row):
        binding = json.loads(row["workflow"]) if row["workflow"] else None
        definition = binding["definition"] if binding else None
        step = definition["steps"][row["workflow_step"]] if definition and row["workflow_step"] < len(definition["steps"]) else None
        return {"id": row["id"], "project": row["project"], "status": row["status"],
                "station_id": row["station_id"], "version": row["workflow_version"],
                "workflow_id": binding["id"] if binding else None,
                "workflow_revision": binding["revision"] if binding else None,
                "workflow_name": definition["name"] if definition else None, "step": step,
                "production_authorized": bool(row["workflow_authorized"]),
                "physical_done": bool(row["physical_done"]),
                "handoff_claimed": bool(row["handoff_claimed"])}

    def station(self, station_id):
        if not isinstance(station_id, str):
            return None
        with self._db() as db:
            row = db.execute("SELECT * FROM laser_stations WHERE id=?", (station_id,)).fetchone()
        return self._public_station(row) if row else None

    def snapshot(self):
        with self._db() as db:
            db.execute("BEGIN")
            stations = [self._public_station(row) for row in db.execute("SELECT * FROM laser_stations ORDER BY id LIMIT 100")]
            jobs = [self._public_job(row) for row in db.execute(
                "SELECT * FROM jobs WHERE status!='done' ORDER BY (workflow IS NULL), created_at DESC LIMIT 100")]
        return {"stations": stations, "jobs": jobs}

    def _job(self, db, job_id):
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ValueError("Laserjobbet findes ikke.")
        return row

    def _station(self, db, station_id):
        row = db.execute("SELECT * FROM laser_stations WHERE id=?", (station_id,)).fetchone()
        if row is None:
            raise ValueError("Laserstationen findes ikke.")
        return row

    def _unfinished(self, db, station_id, exclude=None):
        return db.execute("SELECT 1 FROM jobs WHERE station_id=? AND workflow IS NOT NULL AND workflow_complete=0 AND id!=? LIMIT 1",
                          (station_id, exclude or "")).fetchone() is not None

    def execute(self, actor, envelope):
        actor_id = self._actor(actor)
        if not isinstance(envelope, dict) or set(envelope) != {"action", "params", "expected_version", "request_id"}:
            raise ValueError("Ugyldig workflowkommando.")
        action, params, expected, request_id = envelope["action"], envelope["params"], envelope["expected_version"], envelope["request_id"]
        if not isinstance(request_id, str) or not _ID.fullmatch(request_id) or type(expected) is not int or expected < 0:
            raise ValueError("Ugyldig request-ID eller version.")
        if not isinstance(action, str) or action not in {"laser.station.create", "laser.station.update", "laser.workflow.assign",
                          "laser.job.bind", "laser.job.advance", "laser.job.finish", "laser.job.cancel"}:
            raise ValueError("Ukendt laserworkflowhandling.")
        digest = self._digest(envelope)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT digest,response FROM laser_workflow_commands WHERE actor=? AND request_id=?",
                             (actor_id, request_id)).fetchone()
            if old:
                if old[0] != digest:
                    raise ConfigConflict("Request-ID er allerede brugt til en anden ændring.")
                response = json.loads(old[1])
                result = response['result']
            else:
                result = self._apply(db, actor_id, action, params, expected)
                response = {"ok": True, "action": action, "result": result}
                db.execute("INSERT INTO laser_workflow_commands VALUES(?,?,?,?)",
                           (actor_id, request_id, digest, json.dumps(response, ensure_ascii=False)))
                target = result.get("id") if isinstance(result, dict) else None
                db.execute("INSERT INTO laser_workflow_audit VALUES(?,?,?,?,?,?)",
                           (time.time(), actor_id, action, target, request_id, "applied"))
        if action in {"laser.job.advance", "laser.job.finish", "laser.job.cancel"} and result.get("status") == "done":
            with self._db() as db:
                cleanup_terminal_payload(db, result["id"])
        return response

    def _apply(self, db, actor, action, params, expected):
        if not isinstance(params, dict):
            raise ValueError("params skal være et objekt.")
        if action == "laser.station.create":
            if set(params) != {"id", "name", "location", "model", "enabled"} or expected != 0 or params["enabled"] is not False:
                raise ValueError("Ugyldig ny laserstation.")
            _station_id(params["id"])
            if db.execute("SELECT count(*) FROM laser_stations").fetchone()[0] >= 100: raise ValueError("Der er allerede 100 laserstationer.")
            if not isinstance(params["model"], str) or params["model"] not in _MODELS: raise ValueError("Ugyldig lasermodel.")
            if type(params["enabled"]) is not bool: raise ValueError("Ugyldig stationopsætning.")
            name, location = _text(params["name"], 80), _location(params["location"])
            if db.execute("SELECT 1 FROM laser_stations WHERE id=?", (params["id"],)).fetchone(): raise ValueError("Station-id findes allerede.")
            db.execute("INSERT INTO laser_stations VALUES(?,?,?,?,?,?,?)", (params["id"], name, location, params["model"], 0, 1, None))
            return self._public_station(db.execute("SELECT * FROM laser_stations WHERE id=?", (params["id"],)).fetchone())
        if action == "laser.station.update":
            if set(params) != {"id", "name", "location", "model", "enabled"}: raise ValueError("Ugyldig stationændring.")
            _station_id(params.get("id"))
            row = self._station(db, params["id"])
            if row["version"] != expected: raise ConfigConflict("Stationen er ændret. Hent den aktuelle version.")
            if self._unfinished(db, params["id"]): raise ValueError("Stationen har et åbent workflowjob.")
            if not isinstance(params["model"], str) or params["model"] not in _MODELS or type(params["enabled"]) is not bool: raise ValueError("Ugyldig stationopsætning.")
            db.execute("UPDATE laser_stations SET name=?,location=?,model=?,enabled=?,version=version+1 WHERE id=?",
                       (_text(params["name"], 80), _location(params["location"]), params["model"], int(params["enabled"]), params["id"]))
            return self._public_station(db.execute("SELECT * FROM laser_stations WHERE id=?", (params["id"],)).fetchone())
        if action == "laser.workflow.assign":
            if set(params) != {"id", "workflow"}: raise ValueError("Ugyldig workflowtildeling.")
            _station_id(params.get("id"))
            row = self._station(db, params["id"])
            if row["version"] != expected: raise ConfigConflict("Stationen er ændret. Hent den aktuelle version.")
            workflow = None if params["workflow"] is None else _binding(params["workflow"])
            db.execute("UPDATE laser_stations SET workflow=?,version=version+1 WHERE id=?",
                       (json.dumps(workflow, ensure_ascii=False) if workflow else None, params["id"]))
            return self._public_station(db.execute("SELECT * FROM laser_stations WHERE id=?", (params["id"],)).fetchone())
        if action == "laser.job.bind":
            if set(params) != {"id", "station_id"} or expected != 0: raise ValueError("Ugyldig jobbinding.")
            _job_id(params.get("id"))
            _station_id(params.get("station_id"))
            station = self._station(db, params["station_id"])
            if not station["enabled"] or not station["workflow"]: raise ValueError("Stationen skal være aktiv og have et workflow.")
            job = self._job(db, params["id"])
            if job["status"] not in {"received", "ready"} or not job["payload_available"]:
                raise ValueError("Laserjobbet kan ikke bindes.")
            if job["workflow"]:
                raise ConfigConflict("Laserjobbet er allerede bundet.")
            binding = _binding(json.loads(station["workflow"]))
            db.execute("UPDATE jobs SET workflow=?,workflow_model=?,workflow_step=1,workflow_version=1,workflow_authorized=0,physical_done=0,handoff_claimed=0,workflow_complete=0,station_id=? WHERE id=?",
                       (json.dumps(binding, ensure_ascii=False), station["model"], params["station_id"], params["id"]))
            return self._public_job(self._job(db, params["id"]))
        _job_id(params.get("id"))
        job = self._job(db, params["id"])
        if not job["workflow"]: raise ValueError("Laserjobbet har ikke et workflow.")
        if expected != job["workflow_version"]: raise ConfigConflict("Workflowjobbet er ændret. Hent den aktuelle version.")
        binding = _binding(json.loads(job["workflow"]))
        steps = binding["definition"]["steps"]
        if action == "laser.job.cancel" and job["physical_done"] and not job["workflow_authorized"] and not job["workflow_complete"]:
            raise ValueError("Laserjobbet er allerede aflyst.")
        if job["workflow_complete"] or job["workflow_step"] >= len(steps):
            raise ValueError("Workflowjobbet er allerede afsluttet.")
        if action == "laser.job.advance":
            if set(params) != {"id", "step_id", "confirmed"} or params["confirmed"] is not True: raise ValueError("Bekræft det aktuelle workflowtrin.")
            step = steps[job["workflow_step"]]
            if params["step_id"] != step["id"]: raise ConfigConflict("Workflowtrinet er ikke aktuelt.")
            if step["role"] == "teacher" and not self._teacher(actor): raise PermissionError("Dette trin kræver lærerens egen bekræftelse.")
            production_index = next(i for i, item in enumerate(steps) if item["action"] == "produce")
            if job["workflow_step"] > production_index and not job["physical_done"]:
                raise ValueError("Den fysiske laserafslutning mangler.")
            if step["action"] == "approve":
                # Approval locks image methods. Validate the same prepared
                # export now, before advancing the workflow transaction.
                self.inbox.validate_prepared(db, job["id"])
            authorized = int(job["workflow_authorized"] or step["action"] == "produce")
            complete = int(step["action"] == "clear")
            finished_at = datetime.now(timezone.utc).isoformat() if complete else None
            if complete:
                db.execute("UPDATE jobs SET workflow_step=workflow_step+1,workflow_version=workflow_version+1,workflow_authorized=?,workflow_complete=1,status='done',finished_at=? WHERE id=?",
                           (authorized, finished_at, job["id"]))
            else:
                simulated_handoff = bool(getattr(self.inbox, 'simulation', False) and step['action'] == 'produce')
                db.execute("UPDATE jobs SET workflow_step=workflow_step+1,workflow_version=workflow_version+1,workflow_authorized=?,workflow_complete=0,status=?,handoff_claimed=CASE WHEN ? THEN 1 ELSE handoff_claimed END WHERE id=?",
                           (authorized, job["status"], int(simulated_handoff), job["id"]))
        elif action == "laser.job.finish":
            if not self._teacher(actor): raise PermissionError("Dette trin kræver lærerens egen bekræftelse.")
            if set(params) != {"id", "confirmed"} or params["confirmed"] is not True or job["physical_done"] or not job["workflow_authorized"] or not job["handoff_claimed"]: raise ValueError("Lærerbekræftelse og overførsel kræves.")
            db.execute("UPDATE jobs SET physical_done=1,workflow_version=workflow_version+1 WHERE id=?", (job["id"],))
        else:
            if not self._teacher(actor): raise PermissionError("Dette trin kræver lærerens egen bekræftelse.")
            if set(params) != {"id", "confirmed"} or params["confirmed"] is not True or job["workflow_complete"]: raise ValueError("Læreren skal aflyse laserjobbet.")
            db.execute("UPDATE jobs SET physical_done=1,workflow_authorized=0,workflow_step=?,workflow_version=workflow_version+1 WHERE id=?", (len(steps) - 1, job["id"]))
        return self._public_job(self._job(db, job["id"]))

    def check_export(self, station_id, model, ids):
        if (not isinstance(station_id, str) or len(station_id) > 40 or not _STATION.fullmatch(station_id) or not isinstance(model, str) or model not in _MODELS or
                not isinstance(ids, list) or not ids or len(ids) > 100 or any(not isinstance(i, str) or not _ID.fullmatch(i) for i in ids) or
                len(set(ids)) != len(ids)):
            raise ValueError("Vælg gyldige laserjob.")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            station = db.execute("SELECT * FROM laser_stations WHERE id=?", (station_id,)).fetchone()
            jobs = [self._job(db, i) for i in ids]
            if any(j['status'] not in {'received', 'ready'} or not j['payload_available'] for j in jobs):
                raise ValueError('Laserjobbet er afsluttet eller mangler tegningen.')
            if station is None:
                if any(j['workflow'] for j in jobs): raise ValueError("Laserjobbet har en registreret workflowbinding.")
                return {"managed": False, "station_id": station_id, "ids": ids}
            if not station["enabled"] or station["model"] != model: raise ValueError("Stationen er ikke klar til denne lasermodel.")
            if any(j["status"] not in {"received", "ready"} or not j["payload_available"] or not j["workflow"] or j["station_id"] != station_id or j["workflow_model"] != model or not j["workflow_authorized"] or j["physical_done"] for j in jobs): raise ValueError("Laserjobbet er ikke godkendt til overførsel.")
            if self._unfinished_claim(db, station_id, ids): raise ValueError("En anden laserplade er allerede overført.")
            db.executemany("UPDATE jobs SET handoff_claimed=1 WHERE id=?", ((i,) for i in ids))
        return {"managed": True, "station_id": station_id, "ids": ids}

    @staticmethod
    def _unfinished_claim(db, station_id, ids):
        return db.execute("SELECT 1 FROM jobs WHERE station_id=? AND handoff_claimed=1 AND workflow_complete=0 AND id NOT IN (%s) LIMIT 1" % ','.join('?' * len(ids)),
                          (station_id, *ids)).fetchone() is not None

    def legacy_status(self, job_id, status, actor, db=None):
        """Handle the old Inbox status endpoint without bypassing a pinned job.

        ``db`` is accepted so Inbox.status can perform its legacy status check,
        mutation, and ordinary status transition inside one BEGIN IMMEDIATE.
        """
        _job_id(job_id)
        actor_id = self._actor(actor)
        if not self._teacher(actor_id):
            raise PermissionError("Lærerbekræftelse kræves.")
        if db is None:
            with self._db() as owned:
                owned.execute("BEGIN IMMEDIATE")
                return self._legacy_status_db(owned, job_id, status, actor_id)
        db.row_factory = sqlite3.Row
        return self._legacy_status_db(db, job_id, status, actor_id)

    def _legacy_status_db(self, db, job_id, status, actor_id):
        row = self._job(db, job_id)
        if not row["workflow"]:
            return False
        if status != "done":
            raise ValueError("Et bundet laserjob kan ikke springe workflowtrin over.")
        if row["workflow_complete"] or row["physical_done"]:
            return True
        if not row["workflow_authorized"] or not row["handoff_claimed"]:
            raise PermissionError("Lærerbekræftelse og overførsel kræves.")
        db.execute("UPDATE jobs SET physical_done=1,workflow_version=workflow_version+1 WHERE id=?", (job_id,))
        db.execute("INSERT INTO laser_workflow_audit VALUES(?,?,?,?,?,?)",
                   (time.time(), actor_id, "laser.job.finish", job_id, "legacy:" + job_id, "applied"))
        return True
