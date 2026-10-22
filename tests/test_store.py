"""F2: the bridge's job store (pure SQLite, runs anywhere)."""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.bridge import store as st  # noqa: E402

EV = [{"session": "root", "sql": "SELECT 1", "rows": 1, "elapsed_ms": 3}]
CONTRACT = {"findings": [{"claim": "a", "confidence": "high", "evidence": EV},
                         {"claim": "b", "confidence": "low", "evidence": EV}],
            "ruled_out": [{"explanation": "c", "reason": "d", "evidence": EV}]}


@pytest.fixture
def store(tmp_path):
    s = st.Store(tmp_path)
    yield s
    s.close()


def test_migrates_a_new_file_and_reopens_without_changes(tmp_path):
    first = st.Store(tmp_path)
    assert (first.migrated_from, first.version()) == (0, st.SCHEMA_VERSION)
    first.close()
    again = st.Store(tmp_path)
    assert (again.migrated_from, again.version()) == (st.SCHEMA_VERSION, st.SCHEMA_VERSION)
    again.close()
    tables = {r[0] for r in sqlite3.connect(tmp_path / st.DB_NAME).execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"investigations", "events", "findings"} <= tables


def test_refuses_a_file_from_a_newer_bridge(tmp_path):
    db = sqlite3.connect(tmp_path / st.DB_NAME)
    db.execute(f"PRAGMA user_version = {st.SCHEMA_VERSION + 1}")
    db.close()
    with pytest.raises(RuntimeError, match="schema"):
        st.Store(tmp_path)


def test_journal_mode_is_safe_for_network_filesystems(store):
    assert store._db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def test_investigation_lifecycle_and_events(store):
    inv = store.create_investigation("Why?", scope={"table": "payment_transactions"}, children=3)
    assert inv["status"] == st.QUEUED and inv["scope"] == {"table": "payment_transactions"}
    assert re.fullmatch(r"inv-\d{8}-\d{6}", inv["id"]) and inv["started_at"] is None
    store.update_investigation(inv["id"], run_dir="/x", acp_session_id="s1")
    running = store.set_status(inv["id"], st.RUNNING)
    assert running["started_at"] and not running["finished_at"]
    store.add_event(inv["id"], "child_spawned", {"name": "slice-1"}, session="root")
    done = store.set_status(inv["id"], st.DONE, outcome="findings written")
    assert done["finished_at"] and done["outcome"] == "findings written" and done["run_dir"] == "/x"
    kinds = [(e["kind"], e["data"].get("status")) for e in store.events(inv["id"])]
    assert kinds == [("status", "queued"), ("status", "running"), ("child_spawned", None), ("status", "done")]
    last = store.events(inv["id"])[1]["id"]
    assert [e["kind"] for e in store.events(inv["id"], after_id=last)] == ["child_spawned", "status"]


def test_terminal_status_is_final_and_bad_input_is_refused(store):
    inv = store.create_investigation("q")
    store.set_status(inv["id"], st.CANCELLED)
    with pytest.raises(ValueError, match="already cancelled"):
        store.set_status(inv["id"], st.RUNNING)
    with pytest.raises(ValueError):
        store.set_status(inv["id"], "finished")
    with pytest.raises(ValueError):
        store.update_investigation(inv["id"], status="done")
    with pytest.raises(KeyError):
        store.set_status("inv-nope", st.RUNNING)


def test_findings_round_trip_and_replace(store):
    inv = store.create_investigation("q")
    store.set_findings(inv["id"], CONTRACT)
    assert store.get_findings(inv["id"]) == CONTRACT
    store.set_findings(inv["id"], {"findings": CONTRACT["findings"][:1], "ruled_out": []})
    assert store.get_findings(inv["id"]) == {"findings": CONTRACT["findings"][:1], "ruled_out": []}


def test_findings_write_is_all_or_nothing(store):
    inv = store.create_investigation("q")
    store.set_findings(inv["id"], CONTRACT)
    bad = {"findings": [{"claim": "x", "confidence": "certain", "evidence": EV}]}  # violates the CHECK
    with pytest.raises(sqlite3.IntegrityError):
        store.set_findings(inv["id"], bad)
    assert store.get_findings(inv["id"]) == CONTRACT


def test_list_newest_first_with_status_filter_and_followup_link(store):
    a = store.create_investigation("first")
    b = store.create_investigation("second", parent_id=a["id"])
    store.set_status(b["id"], st.RUNNING)
    assert [i["id"] for i in store.list_investigations()] == [b["id"], a["id"]]
    assert [i["id"] for i in store.list_investigations(status=st.RUNNING)] == [b["id"]]
    assert store.get_investigation(b["id"])["parent_id"] == a["id"]
    assert store.count() == {"queued": 1, "running": 1}


def test_ids_in_the_same_second_take_the_next_free_second(store):
    ids = [store.create_investigation(str(i))["id"] for i in range(3)]
    assert len(set(ids)) == 3 and ids == sorted(ids)
