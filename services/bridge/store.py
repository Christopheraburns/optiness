"""The bridge's job store (BURNDOWN F2): investigations, their events, their findings.

One SQLite file under BRIDGE_DATA_DIR, opened and migrated when the bridge starts.
The bridge is the only writer (the console reads run files, not this database).

Rollback journal, not WAL: on Workbench BRIDGE_DATA_DIR is on the project
filesystem, a network mount where WAL's shared-memory index is unsafe. With one
writing process the journal costs nothing that matters here.

Migrations are append-only: MIGRATIONS[i] takes the schema from version i to
i + 1, tracked in `PRAGMA user_version`. Never edit a released migration.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_NAME = "bridge.db"

QUEUED, RUNNING, DONE, FAILED, TIMEOUT, CANCELLED = "queued", "running", "done", "failed", "timeout", "cancelled"
STATUSES = (QUEUED, RUNNING, DONE, FAILED, TIMEOUT, CANCELLED)
TERMINAL = frozenset({DONE, FAILED, TIMEOUT, CANCELLED})

MIGRATIONS = (
    # 0 -> 1: the F2 schema.
    f"""
    CREATE TABLE investigations (
        id             TEXT PRIMARY KEY,
        question       TEXT NOT NULL,
        scope          TEXT NOT NULL DEFAULT '{{}}',          -- JSON object from start_investigation
        status         TEXT NOT NULL DEFAULT '{QUEUED}'
                       CHECK (status IN ({", ".join(f"'{s}'" for s in STATUSES)})),
        outcome        TEXT,                                 -- runner's one-line outcome
        error          TEXT,
        parent_id      TEXT REFERENCES investigations(id),   -- ask_followup lineage
        run_dir        TEXT,
        acp_session_id TEXT,
        root_model     TEXT,
        child_model    TEXT,
        children       INTEGER,
        created_at     TEXT NOT NULL,
        started_at     TEXT,
        finished_at    TEXT,
        updated_at     TEXT NOT NULL
    ) STRICT;
    CREATE INDEX investigations_created ON investigations (created_at DESC);

    CREATE TABLE events (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        investigation_id TEXT NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
        ts               TEXT NOT NULL,
        kind             TEXT NOT NULL,       -- status, tool_call, child_spawned, child_settled, supervisor, ...
        session          TEXT,                -- 'root' or a child's session name
        data             TEXT NOT NULL DEFAULT '{{}}'
    ) STRICT;
    CREATE INDEX events_by_investigation ON events (investigation_id, id);

    CREATE TABLE findings (
        investigation_id TEXT NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
        kind             TEXT NOT NULL CHECK (kind IN ('finding', 'ruled_out')),
        position         INTEGER NOT NULL,
        statement        TEXT NOT NULL,       -- the claim, or the rejected explanation
        confidence       TEXT CHECK (confidence IN ('high', 'medium', 'low')),
        reason           TEXT,                -- why a ruled-out explanation was rejected
        evidence         TEXT NOT NULL,       -- JSON list of {{session, sql, rows, elapsed_ms}}
        PRIMARY KEY (investigation_id, kind, position)
    ) STRICT;
    """,
)
SCHEMA_VERSION = len(MIGRATIONS)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id(taken=lambda _id: False) -> str:
    """`inv-YYYYMMDD-HHMMSS` in UTC: the run id format the runner and the console use, so one id
    names the investigation, its run directory and its console entry. A second already
    `taken` moves to the next free second."""
    t = datetime.now(timezone.utc).replace(microsecond=0)
    while taken(candidate := f"inv-{t:%Y%m%d-%H%M%S}"):
        t += timedelta(seconds=1)
    return candidate


class Store:
    """Thread-safe access to the job store. Every method is one short transaction."""

    def __init__(self, data_dir: Path | str):
        self.path = Path(data_dir) / DB_NAME
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode = DELETE")
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA busy_timeout = 5000")
        self.migrated_from = self.migrate()

    # ---------------------------------------------------------------- schema
    def version(self) -> int:
        return self._db.execute("PRAGMA user_version").fetchone()[0]

    def migrate(self) -> int:
        """Apply pending migrations; returns the version found. A newer file is refused."""
        with self._lock:
            found = self.version()
            if found > SCHEMA_VERSION:
                raise RuntimeError(f"{self.path} is schema v{found}; this bridge knows v{SCHEMA_VERSION}")
            for v in range(found, SCHEMA_VERSION):
                with self._transaction():
                    for stmt in filter(str.strip, MIGRATIONS[v].split(";")):
                        self._db.execute(stmt)
                    self._db.execute(f"PRAGMA user_version = {v + 1}")
            return found

    @contextmanager
    def _transaction(self):
        """BEGIN IMMEDIATE ... COMMIT, rolled back on any error. Caller holds the lock."""
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # --------------------------------------------------------- investigations
    def create_investigation(self, question: str, *, scope: dict | None = None, parent_id: str | None = None,
                             root_model: str | None = None, child_model: str | None = None,
                             children: int | None = None) -> dict:
        ts = now()
        with self._lock, self._transaction():
            inv_id = new_id(lambda i: self._db.execute(
                "SELECT 1 FROM investigations WHERE id = ?", (i,)).fetchone() is not None)
            self._db.execute(
                "INSERT INTO investigations (id, question, scope, parent_id, root_model, child_model, children,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (inv_id, question, json.dumps(scope or {}), parent_id, root_model, child_model, children, ts, ts))
            self._event(inv_id, "status", None, {"status": QUEUED}, ts)
        return self.get_investigation(inv_id)

    def get_investigation(self, inv_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM investigations WHERE id = ?", (inv_id,)).fetchone()
        return _investigation(row) if row else None

    def list_investigations(self, limit: int = 20, status: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM investigations", []
        if status:
            sql, args = sql + " WHERE status = ?", [status]
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY created_at DESC, rowid DESC LIMIT ?", (*args, limit)).fetchall()
        return [_investigation(r) for r in rows]

    def count(self) -> dict[str, int]:
        """Investigations per status."""
        with self._lock:
            rows = self._db.execute("SELECT status, COUNT(*) FROM investigations GROUP BY status").fetchall()
        return {r[0]: r[1] for r in rows}

    def update_investigation(self, inv_id: str, **fields) -> dict:
        """Set columns (run_dir, acp_session_id, outcome, error, ...). Use set_status for status."""
        allowed = {"run_dir", "acp_session_id", "outcome", "error", "root_model", "child_model", "children"}
        if not fields or not set(fields) <= allowed:
            raise ValueError(f"updatable fields: {sorted(allowed)}")
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            cur = self._db.execute(f"UPDATE investigations SET {cols}, updated_at = ? WHERE id = ?",
                                   (*fields.values(), now(), inv_id))
        if not cur.rowcount:
            raise KeyError(inv_id)
        return self.get_investigation(inv_id)

    def set_status(self, inv_id: str, status: str, *, outcome: str | None = None, error: str | None = None) -> dict:
        """Move an investigation to `status`, stamping start/finish times and logging an event.

        A terminal status is final: changing it raises ValueError.
        """
        if status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        ts = now()
        with self._lock:
            row = self._db.execute("SELECT status FROM investigations WHERE id = ?", (inv_id,)).fetchone()
            if row is None:
                raise KeyError(inv_id)
            if row["status"] in TERMINAL and row["status"] != status:
                raise ValueError(f"{inv_id} is already {row['status']}")
            with self._transaction():
                self._set_status(inv_id, status, outcome, error, ts)
        return self.get_investigation(inv_id)

    def _set_status(self, inv_id: str, status: str, outcome: str | None, error: str | None, ts: str) -> None:
        self._db.execute(
                "UPDATE investigations SET status = ?, outcome = COALESCE(?, outcome), error = COALESCE(?, error),"
                " started_at = CASE WHEN ? = 'running' THEN COALESCE(started_at, ?) ELSE started_at END,"
                " finished_at = CASE WHEN ? IN ('done', 'failed', 'timeout', 'cancelled') THEN ? ELSE finished_at END,"
                " updated_at = ? WHERE id = ?",
                (status, outcome, error, status, ts, status, ts, ts, inv_id))
        self._event(inv_id, "status", None,
                    {k: v for k, v in (("status", status), ("outcome", outcome), ("error", error)) if v}, ts)

    # ----------------------------------------------------------------- events
    def add_event(self, inv_id: str, kind: str, data: dict | None = None, session: str | None = None) -> int:
        with self._lock:
            return self._event(inv_id, kind, session, data or {}, now())

    def _event(self, inv_id: str, kind: str, session: str | None, data: dict, ts: str) -> int:
        cur = self._db.execute("INSERT INTO events (investigation_id, ts, kind, session, data) VALUES (?, ?, ?, ?, ?)",
                               (inv_id, ts, kind, session, json.dumps(data)))
        return cur.lastrowid

    def events(self, inv_id: str, after_id: int = 0, limit: int = 500) -> list[dict]:
        """Events in order; pass the last id seen as `after_id` to page or poll."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM events WHERE investigation_id = ? AND id > ? ORDER BY id LIMIT ?",
                (inv_id, after_id, limit)).fetchall()
        return [{**dict(r), "data": json.loads(r["data"])} for r in rows]

    # --------------------------------------------------------------- findings
    def set_findings(self, inv_id: str, contract: dict) -> None:
        """Replace an investigation's findings with a validated E10 contract
        (services.common.findings.parse output), atomically."""
        rows = [(inv_id, "finding", i, f["claim"], f["confidence"], None, json.dumps(f["evidence"]))
                for i, f in enumerate(contract["findings"])]
        rows += [(inv_id, "ruled_out", i, r["explanation"], None, r["reason"], json.dumps(r["evidence"]))
                 for i, r in enumerate(contract.get("ruled_out", []))]
        with self._lock, self._transaction():
            self._db.execute("DELETE FROM findings WHERE investigation_id = ?", (inv_id,))
            self._db.executemany("INSERT INTO findings VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            self._event(inv_id, "findings", None,
                        {"findings": len(contract["findings"]), "ruled_out": len(contract.get("ruled_out", []))},
                        now())

    def get_findings(self, inv_id: str) -> dict:
        """The contract shape back: {"findings": [...], "ruled_out": [...]}."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM findings WHERE investigation_id = ? ORDER BY kind, position", (inv_id,)).fetchall()
        out = {"findings": [], "ruled_out": []}
        for r in rows:
            ev = json.loads(r["evidence"])
            if r["kind"] == "finding":
                out["findings"].append({"claim": r["statement"], "confidence": r["confidence"], "evidence": ev})
            else:
                out["ruled_out"].append({"explanation": r["statement"], "reason": r["reason"], "evidence": ev})
        return out


def _investigation(row: sqlite3.Row) -> dict:
    return {**dict(row), "scope": json.loads(row["scope"])}
