#!/usr/bin/env python
"""Run one investigation: render the task template and drive a daemon-backed root over ACP.

Runs inside the bridge container (`make investigate Q="..."`) or a Workbench session.
The root gets `config/templates/investigation-task.md` as its prompt, in
AGENT_WORKDIR so AGENTS.md (the method) loads; children are spawned through the
partition-investigator spec. `--mode acp` is used because it is daemon-backed:
`-p`, `--mode json` and `--mode rpc` have no RLM child runtime.

The root ends its turn after spawning and the children's messages start its
next turns, so the first turn ending does not mean the run is done. The run is
done when `findings.md` appears in the run directory, or at the timeout.
Everything the session emits is kept in `events.jsonl` next to the task.

This is the engine the bridge (BURNDOWN F3) will reuse.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from string import Template

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from services.common.env import settings  # noqa: E402

TASK_TEMPLATE = REPO / "config" / "templates" / "investigation-task.md"


def agents_busy() -> bool:
    """True while any Prime Agent session is streaming or not idle (the root or a child).

    Local runs are one at a time, so "any agent" is this run's agents; the bridge
    (F3) needs to scope this to the run's own root and children.
    """
    out = subprocess.run(["prime-agent", "sessions", "--json"], capture_output=True, text=True, timeout=60)
    try:
        doc = json.loads(out.stdout or "{}")
    except json.JSONDecodeError:
        return True
    rows = doc if isinstance(doc, list) else next((v for v in doc.values() if isinstance(v, list)), [])
    return any(r.get("isStreaming") or r.get("activity") not in (None, "idle") for r in rows)


def render_task(question: str, run_id: str, run_dir: Path, children: int, child_model: str) -> str:
    return Template(TASK_TEMPLATE.read_text()).substitute(
        question=question.strip(), run_id=run_id, run_dir=str(run_dir), children=children,
        child_model=child_model).strip()


class AcpSession:
    """Minimal ACP client: JSON-RPC over the prime-agent process's stdio."""

    def __init__(self, cwd: Path, provider: str, model: str, events: Path):
        self.proc = subprocess.Popen(
            ["prime-agent", "--mode", "acp", "--provider", provider, "--model", model],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1, cwd=cwd)
        self.replies: dict[int, dict | None] = {}
        self.last_event = time.time()
        self.tool_calls = 0
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
            update = msg.get("params", {}).get("update", {}) if msg.get("method") == "session/update" else {}
            if update.get("sessionUpdate") == "tool_call":
                self.tool_calls += 1

    def request(self, rid: int, method: str, params: dict) -> None:
        self.replies[rid] = None
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}) + "\n")
        self.proc.stdin.flush()

    def wait(self, rid: int, timeout: float) -> dict | None:
        end = time.time() + timeout
        while self.replies.get(rid) is None and time.time() < end and self.proc.poll() is None:
            time.sleep(0.2)
        return self.replies.get(rid)

    def close(self) -> None:
        self.proc.kill()
        self._events.close()


def main() -> int:
    s = settings()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--question", required=True)
    p.add_argument("--children", type=int, default=2, help="Subagents the root should use")
    p.add_argument("--child-model", default=s.child_model.selector)
    p.add_argument("--timeout", type=float, default=float(s.investigation_timeout_s), help="Seconds for the whole run")
    a = p.parse_args()
    if not 1 <= a.children <= s.max_children:
        raise SystemExit(f"--children must be 1..{s.max_children} (MAX_CHILDREN)")

    run_id = "inv-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = s.agent_workdir / "runs" / run_id
    run_dir.mkdir(parents=True)
    task = render_task(a.question, run_id, run_dir, a.children, a.child_model)
    (run_dir / "task.md").write_text(task + "\n")
    print(f"[investigate] {run_id}: root {s.root_model.selector}, {a.children} x {a.child_model}; {run_dir}", flush=True)

    acp = AcpSession(s.agent_workdir, s.root_model.provider, s.root_model.model, run_dir / "events.jsonl")
    started = time.time()
    outcome = "timeout"
    try:
        acp.request(1, "initialize", {"protocolVersion": 1, "clientCapabilities": {}})
        if acp.wait(1, 120) is None:
            raise SystemExit("[investigate] ACP initialize failed (is the Prime Agent daemon able to start?)")
        acp.request(2, "session/new", {"cwd": str(s.agent_workdir), "mcpServers": []})
        session_id = ((acp.wait(2, 120) or {}).get("result") or {}).get("sessionId")
        if not session_id:
            raise SystemExit("[investigate] ACP session/new failed")
        (run_dir / "session.json").write_text(json.dumps({"session_id": session_id, "run_id": run_id}) + "\n")
        acp.request(3, "session/prompt", {"sessionId": session_id, "prompt": [{"type": "text", "text": task}]})
        findings = run_dir / "findings.md"
        while time.time() - started < a.timeout:
            time.sleep(5)
            if acp.proc.poll() is not None:
                outcome = "agent process exited"
                break
            # Done only when the findings exist and nobody is still working: an early
            # findings file written before the children reported does not count.
            if findings.exists() and findings.stat().st_size and time.time() - acp.last_event > 10 \
                    and not agents_busy():
                outcome = "findings written"
                break
    finally:
        acp.close()

    elapsed = time.time() - started
    files = sorted(f.name for f in run_dir.iterdir())
    print(f"[investigate] {outcome} after {elapsed:.0f}s; root tool calls {acp.tool_calls}; files {files}", flush=True)
    if outcome != "findings written":
        print("[investigate] the root may still be running: `prime-agent sessions`, stop with `prime-agent stop <id>`")
    return 0 if outcome == "findings written" else 1


if __name__ == "__main__":
    raise SystemExit(main())
