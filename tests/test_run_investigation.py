"""ChildSupervisor (E8): re-prompt children that go idle without reporting, then give up."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import services.common.investigation as ri  # noqa: E402

PROMPT = "Then send the parent one line: `await agent_message.send(\"done: ...\", receiver_role=\"parent\")`"


def transcript(path: Path, *, reported: bool, started: bool = True) -> str:
    lines = [{"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": PROMPT}]}}]
    if started:
        lines.append({"type": "message", "message": {"role": "assistant",
                                                     "content": [{"type": "text", "text": "I'll start."}]}})
    if reported:
        lines.append({"type": "message", "message": {"role": "assistant", "content": [
            {"type": "toolCall", "arguments": {"code": "await agent_message.send('done: x', receiver_role='parent')"}}]}})
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return str(path)


def rows(tmp_path: Path, *, reported: bool, created: str = "2099-01-01T00:00:00Z", started: bool = True) -> list[dict]:
    return [
        {"id": "root1", "rlmDepth": 0, "created": created, "activity": "idle", "isStreaming": False},
        {"id": "kid1", "rlmDepth": 1, "parentActiveSessionId": "root1", "sessionName": "slice-a",
         "activity": "idle", "isStreaming": False,
         "sessionFile": transcript(tmp_path / "kid1.jsonl", reported=reported, started=started)},
    ]


def test_prompt_text_alone_does_not_count_as_a_report(tmp_path):
    assert not ri.child_reported(transcript(tmp_path / "a.jsonl", reported=False))
    assert ri.child_reported(transcript(tmp_path / "b.jsonl", reported=True))


def test_idle_child_is_reprompted_then_given_up(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(ri, "send", lambda agent, msg, follow_up=False: sent.append((agent, follow_up)))
    monkeypatch.setattr(ri, "NUDGE_GRACE_S", 0)
    sup = ri.ChildSupervisor(started=0)
    for _ in range(ri.MAX_NUDGES + 2):
        sup.check(rows(tmp_path, reported=False))
    assert sent == [("kid1", False)] * ri.MAX_NUDGES + [("root1", True)]


def test_reported_busy_or_foreign_children_are_left_alone(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(ri, "send", lambda agent, msg, follow_up=False: sent.append(agent))
    sup = ri.ChildSupervisor(started=time.time())
    sup.check(rows(tmp_path, reported=True))
    busy = rows(tmp_path, reported=False)
    busy[1]["activity"] = "working"
    sup.check(busy)
    sup.check(rows(tmp_path, reported=False, created="2000-01-01T00:00:00Z"))  # another run's root
    assert sent == []


def test_nudges_wait_for_the_grace_period(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(ri, "send", lambda agent, msg, follow_up=False: sent.append(agent))
    sup = ri.ChildSupervisor(started=0)
    sup.check(rows(tmp_path, reported=False))
    sup.check(rows(tmp_path, reported=False))
    assert sent == ["kid1"] and sup.recently_nudged()


def test_supervisor_reports_spawn_reprompt_and_report_once(tmp_path, monkeypatch):
    monkeypatch.setattr(ri, "send", lambda agent, msg, follow_up=False: None)
    events = []
    sup = ri.ChildSupervisor(started=0, emit=lambda kind, data, session=None: events.append((kind, session)))
    sup.check(rows(tmp_path, reported=False))
    sup.check(rows(tmp_path, reported=True))
    sup.check(rows(tmp_path, reported=True))
    assert events == [("daemon_root", "root"), ("child_spawned", "slice-a"), ("child_reprompted", "slice-a"),
                      ("child_reported", "slice-a")]


def test_a_child_that_has_not_taken_its_first_turn_is_not_reprompted(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(ri, "send", lambda agent, msg, follow_up=False: sent.append(agent))
    sup = ri.ChildSupervisor(started=0)
    sup.check(rows(tmp_path, reported=False, started=False))
    assert sent == [] and "kid1" in sup.seen
    sup.check(rows(tmp_path, reported=False, started=True))
    assert sent == ["kid1"]


def test_close_reaps_a_cooperative_and_a_stubborn_process(tmp_path):
    # Closing stdin is enough for a process that exits on EOF; one that ignores EOF and
    # SIGTERM is killed. Either way it is reaped (returncode set), so no zombie remains.
    polite = ri.AcpSession(tmp_path, "p", "m", tmp_path / "a.jsonl", cmd=["cat"])
    polite.close(grace_s=2)
    assert polite.proc.returncode == 0
    stubborn = ri.AcpSession(tmp_path, "p", "m", tmp_path / "b.jsonl",
                             cmd=["sh", "-c", "trap '' TERM; while :; do sleep 0.1; done"])
    t0 = time.time()
    stubborn.close(grace_s=0.3)
    assert stubborn.proc.returncode is not None and time.time() - t0 < 3
