"""F3: the bridge's investigation runner, with a fake engine (no models, no daemon)."""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.bridge import store as st  # noqa: E402
from services.bridge.runner import Runner  # noqa: E402
from services.common import investigation as engine  # noqa: E402

EV = [{"session": "slice-1", "sql": "SELECT 1", "rows": 1, "elapsed_ms": 3}]
CONTRACT = {"findings": [{"claim": "a", "confidence": "high", "evidence": EV}], "ruled_out": []}


def fake(outcome=engine.FINDINGS_WRITTEN, contract=CONTRACT, error=None, wait_for_cancel=False, crash=False):
    calls = []

    def investigate(question, *, children, child_model, run_id, emit, cancelled):
        calls.append((question, children, child_model, run_id))
        emit("run_started", {"run_id": run_id, "run_dir": f"/runs/{run_id}"}, None)
        emit("acp_session", {"session_id": "acp-1"}, "root")
        emit("child_spawned", {"id": "c1"}, "slice-1")
        if crash:
            raise RuntimeError("boom")
        if wait_for_cancel:
            while not cancelled():
                time.sleep(0.01)
            return engine.RunResult(run_id, Path("/runs") / run_id, engine.CANCELLED)
        return engine.RunResult(run_id, Path("/runs") / run_id, outcome, contract=contract, error=error)
    investigate.calls = calls
    return investigate


def wait_status(store, inv_id, statuses, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        inv = store.get_investigation(inv_id)
        if inv["status"] in statuses:
            return inv
        time.sleep(0.02)
    raise AssertionError(f"{inv_id} stuck in {store.get_investigation(inv_id)['status']}")


@pytest.fixture
def store(tmp_path):
    s = st.Store(tmp_path)
    yield s
    s.close()


def test_a_run_records_status_events_and_findings(store):
    inv_fake = fake()
    r = Runner(store, inv_fake)
    inv = r.submit("Why?", children=2)
    done = wait_status(store, inv["id"], st.TERMINAL)
    assert done["status"] == st.DONE and done["outcome"] == engine.FINDINGS_WRITTEN
    assert done["run_dir"] == f"/runs/{inv['id']}" and done["acp_session_id"] == "acp-1"
    assert inv_fake.calls[0][:2] == ("Why?", 2) and inv_fake.calls[0][3] == inv["id"]
    kinds = [(e["kind"], e["session"]) for e in store.events(inv["id"])]
    assert ("child_spawned", "slice-1") in kinds and kinds[-1] == ("status", None)
    assert store.get_findings(inv["id"]) == CONTRACT


@pytest.mark.parametrize("outcome, status", [
    (engine.CONTRACT_INVALID, st.FAILED), (engine.TIMEOUT, st.TIMEOUT),
    ("root went idle without findings (tool calls 0)", st.FAILED)])
def test_outcomes_map_to_statuses(store, outcome, status):
    r = Runner(store, fake(outcome, contract=None, error="why"))
    inv = wait_status(store, r.submit("q", children=1)["id"], st.TERMINAL)
    assert (inv["status"], inv["outcome"], inv["error"]) == (status, outcome, "why")
    assert store.get_findings(inv["id"]) == {"findings": [], "ruled_out": []}


def test_cancel_queued_and_running(store):
    gate = threading.Event()
    slow = fake(wait_for_cancel=True)

    def first_blocks(*a, **k):
        gate.set()
        return slow(*a, **k)
    r = Runner(store, first_blocks)
    running = r.submit("first", children=1)
    queued = r.submit("second", children=1)
    assert gate.wait(5)
    assert r.cancel(queued["id"])["status"] == st.CANCELLED
    r.cancel(running["id"])
    assert wait_status(store, running["id"], st.TERMINAL)["status"] == st.CANCELLED
    time.sleep(0.1)
    assert len(slow.calls) == 1  # the cancelled queued run never started
    assert any(e["kind"] == "cancel_requested" for e in store.events(running["id"]))


def test_a_crash_fails_the_run_and_the_worker_survives(store):
    r = Runner(store, fake(crash=True))
    bad = wait_status(store, r.submit("q", children=1)["id"], st.TERMINAL)
    assert bad["status"] == st.FAILED and "boom" in bad["error"]
    r.investigate = fake()
    assert wait_status(store, r.submit("q2", children=1)["id"], st.TERMINAL)["status"] == st.DONE


def test_recover_fails_interrupted_runs_and_requeues_waiting_ones(store):
    interrupted = store.create_investigation("was running", children=1)
    store.set_status(interrupted["id"], st.RUNNING)
    waiting = store.create_investigation("was queued", children=1, child_model="m")
    r = Runner(store, fake(), ensure_daemon=lambda: True)
    r.recover()
    assert store.get_investigation(interrupted["id"])["outcome"] == "interrupted"
    r.start()
    assert wait_status(store, waiting["id"], st.TERMINAL)["status"] == st.DONE


def test_bad_requests_are_refused(store):
    r = Runner(store, fake())
    with pytest.raises(ValueError):
        r.submit("  ", children=1)
    with pytest.raises(ValueError):
        r.submit("q", children=99)
    with pytest.raises(KeyError):
        r.cancel("inv-nope")


def test_recover_stops_the_orphaned_sessions_of_an_interrupted_run(store):
    inv = store.create_investigation("was running", children=1)
    store.set_status(inv["id"], st.RUNNING)
    store.add_event(inv["id"], "daemon_root", {"id": "root-abc"}, "root")
    stopped = []
    daemon = []
    r = Runner(store, fake(), stop_tree=lambda ids: stopped.extend(ids) or ["child-1", *ids],
               ensure_daemon=lambda: daemon.append(1) or True)
    r.recover()
    assert stopped == ["root-abc"] and daemon == [1]
    after = store.get_investigation(inv["id"])
    assert (after["status"], after["outcome"]) == (st.FAILED, "interrupted")
    assert any(e["kind"] == "sessions_stopped" and e["data"]["ids"] == ["child-1", "root-abc"]
               for e in store.events(inv["id"]))


def test_shutdown_interrupts_the_running_investigation_and_leaves_the_queue(store):
    gate = threading.Event()
    slow = fake(wait_for_cancel=True)

    def first_blocks(*a, **k):
        gate.set()
        return slow(*a, **k)
    r = Runner(store, first_blocks)
    running = r.submit("first", children=1)
    waiting = r.submit("second", children=1)
    assert gate.wait(5)
    assert r.shutdown(timeout=5) is True
    after = store.get_investigation(running["id"])
    assert (after["status"], after["outcome"]) == (st.FAILED, "interrupted")
    time.sleep(0.1)
    assert store.get_investigation(waiting["id"])["status"] == st.QUEUED  # runs after the next start
