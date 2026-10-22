#!/usr/bin/env python
"""Run one investigation from the command line and print its progress.

Runs inside the bridge container (`make investigate Q="..."`) or a Workbench session.
The engine (task rendering, ACP client, child supervisor, findings contract) is
services/common/investigation.py, shared with the bridge (BURNDOWN F3).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from services.common.env import settings  # noqa: E402
from services.common.investigation import (  # noqa: E402,F401  (re-exported for callers and tests)
    CONTRACT_INVALID, FINDINGS_WRITTEN, investigate, render_task)

QUIET = {"root_message", "root_turn_end", "acp_session"}


def print_event(kind: str, data: dict, session: str | None = None) -> None:
    if kind in QUIET:
        return
    if kind == "tool_call":
        first = next((ln for ln in data.get("code", "").splitlines() if ln.strip()), "")
        detail = first[:100]
    else:
        detail = json.dumps(data)[:200]
    print(f"[investigate] {kind}{f' {session}' if session else ''}: {detail}", flush=True)


def main() -> int:
    s = settings()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--question", required=True)
    p.add_argument("--children", type=int, default=2, help="Subagents the root should use")
    p.add_argument("--child-model", default=s.child_model.selector)
    p.add_argument("--timeout", type=float, default=float(s.investigation_timeout_s), help="Seconds for the whole run")
    a = p.parse_args()
    try:
        r = investigate(a.question, children=a.children, child_model=a.child_model, timeout=a.timeout,
                        emit=print_event)
    except ValueError as e:
        raise SystemExit(f"[investigate] {e}")
    if r.contract:
        print(f"[investigate] findings contract ok: {len(r.contract['findings'])} findings, "
              f"{len(r.contract['ruled_out'])} ruled out", flush=True)
    if r.outcome not in (FINDINGS_WRITTEN, CONTRACT_INVALID):
        print("[investigate] the root may still be running: `prime-agent sessions`, stop with `prime-agent stop <id>`")
    return 0 if r.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
