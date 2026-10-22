"""The investigation engine: render the task template and drive a daemon-backed root over ACP.

Used by the CLI (`scripts/run_investigation.py`, `make investigate`) and by the bridge
(`services/bridge/runner.py`, BURNDOWN F3). The root gets
`config/templates/investigation-task.md` as its prompt, in AGENT_WORKDIR so AGENTS.md
(the method) loads; children are spawned through the partition-investigator spec.
`--mode acp` is used because it is daemon-backed: `-p`, `--mode json` and `--mode rpc`
have no RLM child runtime.

The root ends its turn after spawning and the children's messages start its next
turns, so the first turn ending does not mean the run is done. The run is done when
`findings.md` appears in the run directory and nobody is working, or at the timeout.
Everything the session emits is kept in `events.jsonl` next to the task. The report's
closing contract block (E10, services/common/findings.py) is validated and saved as
`findings.json`; a report that breaks the contract fails the run.

Progress is reported through `emit(kind, data, session)`; kinds are listed in EVENT_KINDS.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from typing import Callable

from services.common.env import settings
from services.common.findings import FindingsError, parse as parse_findings

REPO = Path(__file__).resolve().parents[2]
TASK_TEMPLATE = REPO / "config" / "templates" / "investigation-task.md"

FINDINGS_WRITTEN = "findings written"
CONTRACT_INVALID = "findings contract invalid"
TIMEOUT = "timeout"
CANCELLED = "cancelled"

# What emit() can report. `session` is "root" or a child's session name.
EVENT_KINDS = {
    "run_started": "run id, run dir, models, children",
    "acp_session": "the root's ACP session id",
    "tool_call": "the root called a tool (title, code excerpt)",
    "tool_error": "a root tool call failed",
    "root_message": "text the root wrote, one event per response",
    "root_turn_end": "the root finished a response (outcome)",
    "child_spawned": "a child session appeared (name, model)",
    "child_reported": "a child sent its done: message",
    "child_reprompted": "the supervisor re-prompted an idle child (attempt, error)",
    "child_failed": "the supervisor gave up on a child and told the root",
    "error": "the ACP turn failed (message)",
    "daemon_root": "the daemon's id for this run's root session (for cleanup after a restart)",
    "sessions_stopped": "daemon sessions stopped at the end of a failed or cancelled run",
    "run_finished": "outcome, elapsed seconds, root tool calls, files",
}

Emit = Callable[[str, dict, "str | None"], None]

MAX_NUDGES = 2
NUDGE_GRACE_S = 20
CODE_EXCERPT = 2000
CHILD_NUDGE = (
    "Supervisor: you ended your turn without finishing your task. Nothing is done until you "
    "have run your queries, written your output file and sent your `done:` message. Continue "
    "now: call the REPL tool with your next `await lakehouse.query(...)`, and keep going until "
    "the file is written and `await agent_message.send(\"done: ...\", receiver_role=\"parent\")` has run.")


def _no_emit(kind: str, data: dict, session: str | None = None) -> None:
    pass


# ------------------------------------------------------------ daemon sessions
def sessions() -> list[dict] | None:
    """Rows of `prime-agent sessions --json`, or None if they could not be read."""
    out = subprocess.run(["prime-agent", "sessions", "--json"], capture_output=True, text=True, timeout=60)
    try:
        doc = json.loads(out.stdout or "{}")
    except json.JSONDecodeError:
        return None
    return doc if isinstance(doc, list) else next((v for v in doc.values() if isinstance(v, list)), [])


def agents_busy(rows: list[dict] | None = None) -> bool:
    """True while any Prime Agent session is streaming or not idle (the root or a child).

    Runs are one at a time (the CLI by habit, the bridge by its queue), so "any agent"
    is this run's agents.
    """
    rows = sessions() if rows is None else rows
    if rows is None:
        return True
    return any(r.get("isStreaming") or r.get("activity") not in (None, "idle") for r in rows)


def child_progress(session_file: str | None) -> tuple[int, bool]:
    """(assistant turns taken, reported) from a child's transcript.

    Reported means a tool call that sends its report to the parent; only tool-call
    arguments count, since the child's prompt itself mentions agent_message.send.
    """
    try:
        lines = Path(session_file).read_text().splitlines() if session_file else []
    except OSError:
        return 0, False
    turns, reported = 0, False
    for line in lines:
        try:
            msg = json.loads(line).get("message") or {}
        except json.JSONDecodeError:
            continue
        if msg.get("role") != "assistant":
            continue
        turns += 1
        for part in msg.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "toolCall" \
                    and "agent_message.send" in json.dumps(part.get("arguments")):
                reported = True
    return turns, reported


def child_reported(session_file: str | None) -> bool:
    return child_progress(session_file)[1]


def created_after(row: dict, t: float) -> bool:
    try:
        return datetime.fromisoformat(row["created"].replace("Z", "+00:00")).timestamp() >= t
    except (KeyError, AttributeError, ValueError):
        return False


def run_sessions(rows: list[dict], started: float) -> tuple[set[str], list[dict]]:
    """This run's root ids and child rows: roots created after the run started, and their children."""
    roots = {r["id"] for r in rows if not r.get("rlmDepth") and created_after(r, started - 5)}
    return roots, [r for r in rows if r.get("rlmDepth") and r.get("parentActiveSessionId") in roots]


def send(agent: str, message: str, follow_up: bool = False) -> str | None:
    """Message an agent; returns the error text, or None on success.

    No `--from`: it must name an existing session, so the text identifies the supervisor.
    """
    cmd = ["prime-agent", "send", *(["--follow-up"] if follow_up else []), agent, message]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return None if out.returncode == 0 else (out.stderr or out.stdout).strip()


def stop_run(started: float) -> list[str]:
    """Stop this run's children and root in the daemon. Returns the ids stopped."""
    roots, children = run_sessions(sessions() or [], started)
    return stop_sessions([c["id"] for c in children] + sorted(roots))


def ensure_daemon(timeout: float = 60) -> bool:
    """Make sure the Prime Agent daemon is up; True if it answered.

    After a hard kill the daemon is gone and its socket is stale (`prime-agent sessions`
    fails, `doctor --fix` keeps the default socket). An ACP `initialize` starts a fresh
    daemon, which also restores saved sessions, including any interrupted mid-turn: those
    resume working, so callers stop them right after.
    """
    s = settings()
    acp = AcpSession(s.agent_workdir, s.root_model.provider, s.root_model.model, Path(os.devnull))
    try:
        acp.request(1, "initialize", {"protocolVersion": 1, "clientCapabilities": {}})
        return acp.wait(1, timeout) is not None
    finally:
        acp.close()


def stop_tree(root_ids: list[str], wait_s: float = 20) -> list[str]:
    """Stop the given daemon roots and their children (orphans of an interrupted run).

    A freshly started daemon adopts sessions that were mid-turn about a second after it
    boots, so wait up to `wait_s` for each root to appear and stop it (and its children)
    once it does.
    """
    deadline, stopped = time.time() + wait_s, []
    while True:
        rows = sessions() or []
        live = {r["id"] for r in rows}
        targets = [r["id"] for r in rows if r.get("rlmDepth") and r.get("parentActiveSessionId") in root_ids]
        targets += [r for r in root_ids if r in live]
        stopped += stop_sessions([x for x in targets if x not in stopped])
        if all(r in stopped for r in root_ids) or time.time() >= deadline:
            break
        time.sleep(1)
    # Children are adopted around the same time as their root: one more sweep for late ones.
    time.sleep(2)
    late = [r["id"] for r in sessions() or []
            if r.get("rlmDepth") and r.get("parentActiveSessionId") in root_ids and r["id"] not in stopped]
    return stopped + stop_sessions(late)


def stop_sessions(ids: list[str]) -> list[str]:
    for sid in ids:
        subprocess.run(["prime-agent", "stop", sid], capture_output=True, text=True, timeout=60)
    return ids


class ChildSupervisor:
    """Re-prompts children that go idle without reporting; tells the root when one is given up.

    Mistral models sometimes announce a plan and end the turn with no tool call. The
    root is notified the moment a child settles and would write it off, so the runner
    re-prompts the child (`prime-agent send`) up to MAX_NUDGES times and only then sends
    the root a verdict. Also reports each child's spawn and report as events.
    """

    def __init__(self, started: float, emit: Emit = _no_emit):
        self.started = started
        self.emit = emit
        self.nudges: dict[str, int] = {}
        self.last_nudge: dict[str, float] = {}
        self.given_up: set[str] = set()
        self.seen: set[str] = set()
        self.reported: set[str] = set()
        self.roots: set[str] = set()

    def recently_nudged(self) -> bool:
        """A re-prompt is still being picked up, so an idle-looking run is not finished."""
        return any(time.time() - t < NUDGE_GRACE_S + 30 for t in self.last_nudge.values())

    def check(self, rows: list[dict]) -> None:
        roots, children = run_sessions(rows, self.started)
        for rid in sorted(roots - self.roots):
            # The daemon's id for the root (not the ACP session id): what cleanup after a
            # bridge restart needs to find this run's sessions.
            self.roots.add(rid)
            self.emit("daemon_root", {"id": rid}, "root")
        for r in children:
            cid, root = r.get("id"), r.get("parentActiveSessionId")
            name = r.get("sessionName") or cid
            if cid not in self.seen:
                self.seen.add(cid)
                self.emit("child_spawned", {"id": cid, "model": (r.get("model") or {}).get("id")}, name)
            if cid in self.given_up or cid in self.reported:
                continue
            turns, reported = child_progress(r.get("sessionFile"))
            if reported:
                self.reported.add(cid)
                self.emit("child_reported", {"id": cid, "reprompts": self.nudges.get(cid, 0)}, name)
                continue
            # A child that has not answered yet is still waiting for its task, not stopped.
            idle = not r.get("isStreaming") and r.get("activity") in (None, "idle")
            if not turns or not idle or time.time() - self.last_nudge.get(cid, 0) < NUDGE_GRACE_S:
                continue
            if self.nudges.get(cid, 0) < MAX_NUDGES:
                self.nudges[cid] = self.nudges.get(cid, 0) + 1
                self.last_nudge[cid] = time.time()
                err = send(cid, CHILD_NUDGE)
                self.emit("child_reprompted", {"id": cid, "attempt": self.nudges[cid], "of": MAX_NUDGES,
                                               **({"error": err} if err else {})}, name)
            else:
                self.given_up.add(cid)
                err = send(root, f"Supervisor: child {name} stopped without reporting after {MAX_NUDGES} "
                                 "re-prompts. Treat it as failed.", follow_up=True)
                self.emit("child_failed", {"id": cid, "reprompts": MAX_NUDGES,
                                           **({"error": err} if err else {})}, name)


# ------------------------------------------------------------------ ACP client
def render_task(question: str, run_id: str, run_dir: Path, children: int, child_model: str) -> str:
    return Template(TASK_TEMPLATE.read_text()).substitute(
        question=question.strip(), run_id=run_id, run_dir=str(run_dir), children=children,
        child_model=child_model).strip()


class AcpSession:
    """Minimal ACP client: JSON-RPC over the prime-agent process's stdio.

    Keeps every line in `events`, and reports the root's tool calls, text and turn
    ends through `emit`.
    """

    def __init__(self, cwd: Path, provider: str, model: str, events: Path, emit: Emit = _no_emit,
                 cmd: list[str] | None = None):
        self.proc = subprocess.Popen(
            cmd or ["prime-agent", "--mode", "acp", "--provider", provider, "--model", model],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1, cwd=cwd)
        self.replies: dict[int, dict | None] = {}
        self.last_event = time.time()
        self.tool_calls = 0
        self.emit = emit
        self._text: list[str] = []
        self._message_id: str | None = None
        self._events = events.open("w")
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.proc.stdout:
            self._events.write(line)
            self._events.flush()
            self.last_event = time.time()
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") in self.replies:
                self.replies[msg["id"]] = msg
                if msg.get("error"):
                    err = msg["error"]
                    self.emit("error", {"message": (err.get("data") or {}).get("details") or err.get("message")},
                              "root")
            if msg.get("method") == "session/update":
                self._update(msg.get("params", {}).get("update", {}))
        self._flush_text()

    def _update(self, u: dict) -> None:
        kind = u.get("sessionUpdate")
        if kind == "tool_call":
            self.tool_calls += 1
            code = str((u.get("rawInput") or {}).get("code") or "")
            self.emit("tool_call", {"title": u.get("title"), "code": code[:CODE_EXCERPT],
                                    "truncated": len(code) > CODE_EXCERPT}, "root")
        elif kind == "tool_call_update" and u.get("status") == "failed":
            self.emit("tool_error", {"tool_call_id": u.get("toolCallId")}, "root")
        elif kind == "agent_message_chunk":
            if u.get("messageId") != self._message_id:
                self._flush_text()
                self._message_id = u.get("messageId")
            self._text.append(str((u.get("content") or {}).get("text") or ""))
        elif kind == "session_info_update":
            meta = (u.get("_meta") or {}).get("ai.primeintellect.prime-agent") or {}
            if meta.get("phase") == "responseBoundary":
                self._flush_text()
                self.emit("root_turn_end", {"outcome": meta.get("outcome")}, "root")

    def _flush_text(self) -> None:
        text = "".join(self._text).strip()
        self._text = []
        if text:
            self.emit("root_message", {"text": text}, "root")

    def request(self, rid: int, method: str, params: dict) -> None:
        self.replies[rid] = None
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}) + "\n")
        self.proc.stdin.flush()

    def wait(self, rid: int, timeout: float) -> dict | None:
        end = time.time() + timeout
        while self.replies.get(rid) is None and time.time() < end and self.proc.poll() is None:
            time.sleep(0.2)
        return self.replies.get(rid)

    def close(self, grace_s: float = 5) -> None:
        """Close stdin (prime-agent settles the turn and exits), then terminate, then kill;
        always reap, so no defunct process is left behind."""
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        for step in (None, self.proc.terminate, self.proc.kill):
            if step:
                step()
            try:
                self.proc.wait(timeout=grace_s)
                break
            except subprocess.TimeoutExpired:
                continue
        self._events.close()


# --------------------------------------------------------------------- the run
@dataclass
class RunResult:
    run_id: str
    run_dir: Path
    outcome: str
    elapsed_s: float = 0.0
    tool_calls: int = 0
    session_id: str | None = None
    contract: dict | None = None
    error: str | None = None
    files: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.outcome == FINDINGS_WRITTEN


class _Abort(Exception):
    """Startup failed; result.outcome says how."""


def new_run_id() -> str:
    return "inv-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def investigate(question: str, *, children: int, child_model: str | None = None, timeout: float | None = None,
                run_id: str | None = None, emit: Emit = _no_emit,
                cancelled: Callable[[], bool] = lambda: False, poll_s: float = 5) -> RunResult:
    """Run one investigation to completion; never raises for agent-side failures."""
    s = settings()
    child_model = child_model or s.child_model.selector
    timeout = float(timeout or s.investigation_timeout_s)
    if not 1 <= children <= s.max_children:
        raise ValueError(f"children must be 1..{s.max_children} (MAX_CHILDREN)")
    run_id = run_id or new_run_id()
    run_dir = s.agent_workdir / "runs" / run_id
    run_dir.mkdir(parents=True)
    task = render_task(question, run_id, run_dir, children, child_model)
    (run_dir / "task.md").write_text(task + "\n")
    emit("run_started", {"run_id": run_id, "run_dir": str(run_dir), "root_model": s.root_model.selector,
                         "child_model": child_model, "children": children}, None)

    result = RunResult(run_id, run_dir, TIMEOUT)
    started = time.time()
    acp = AcpSession(s.agent_workdir, s.root_model.provider, s.root_model.model, run_dir / "events.jsonl", emit)
    supervisor = ChildSupervisor(started, emit)
    try:
        acp.request(1, "initialize", {"protocolVersion": 1, "clientCapabilities": {}})
        if acp.wait(1, 120) is None:
            result.outcome, result.error = "acp initialize failed", "is the Prime Agent daemon able to start?"
            raise _Abort
        acp.request(2, "session/new", {"cwd": str(s.agent_workdir), "mcpServers": []})
        result.session_id = ((acp.wait(2, 120) or {}).get("result") or {}).get("sessionId")
        if not result.session_id:
            result.outcome = "acp session/new failed"
            raise _Abort
        (run_dir / "session.json").write_text(json.dumps({"session_id": result.session_id, "run_id": run_id}) + "\n")
        emit("acp_session", {"session_id": result.session_id}, "root")
        acp.request(3, "session/prompt", {"sessionId": result.session_id, "prompt": [{"type": "text", "text": task}]})
        findings = run_dir / "findings.md"
        while time.time() - started < timeout:
            time.sleep(poll_s)
            if cancelled():
                result.outcome = CANCELLED
                break
            if acp.proc.poll() is not None:
                result.outcome = "agent process exited"
                break
            rows = sessions()
            if rows is not None:
                supervisor.check(rows)
            # Done only when the findings exist and nobody is still working: an early
            # findings file written before the children reported does not count.
            quiet = time.time() - acp.last_event > 10
            if findings.exists() and findings.stat().st_size and quiet and not agents_busy(rows):
                result.outcome = FINDINGS_WRITTEN
                break
            # The first turn has ended and nothing is running, yet there are no findings:
            # no child will wake the root, so waiting out the timeout gains nothing. Seen
            # when the model writes its tool calls as text instead of calling the tool.
            if acp.replies.get(3) is not None and time.time() - acp.last_event > 30 and not agents_busy(rows) \
                    and not supervisor.recently_nudged():
                result.outcome = f"root went idle without findings (tool calls {acp.tool_calls})"
                break
    except _Abort:
        pass
    finally:
        acp.close()
        if result.outcome != FINDINGS_WRITTEN:
            # Leave a finished run's idle sessions for the Agents View; stop everything else.
            stopped = stop_run(started)
            if stopped:
                emit("sessions_stopped", {"ids": stopped}, None)
        result.elapsed_s = round(time.time() - started, 1)
        result.tool_calls = acp.tool_calls

    if result.outcome == FINDINGS_WRITTEN:
        try:
            result.contract = parse_findings((run_dir / "findings.md").read_text())
            (run_dir / "findings.json").write_text(json.dumps(result.contract, indent=1) + "\n")
        except FindingsError as e:
            result.outcome, result.error = CONTRACT_INVALID, str(e)
    result.files = sorted(f.name for f in run_dir.iterdir())
    emit("run_finished", {"outcome": result.outcome, "elapsed_s": result.elapsed_s, "tool_calls": result.tool_calls,
                          "files": result.files, **({"error": result.error} if result.error else {})}, None)
    return result
