"""A run as a timeline (BURNDOWN H6-H8): what the Investigation feed, the Swarm and Replay draw.

Every time is seconds from the run's start, so the front end can replay any finished run by
moving one cursor. Hypotheses come from the run's own findings contract (claims and ruled-out
explanations), each tied to the queries that produced its evidence: it appears when its first
evidence query ran and resolves when its last one did, so a replay never shows a conclusion
before the agent had the evidence for it. Nothing here knows the planted answer.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from services.console.model import Cell, Investigation, Session

RESULT_PREVIEW = 1_500
MESSAGE_CHARS = 2_000
TITLE_CHARS = 110


def norm_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip().rstrip(";").strip().lower()


def _pattern(cell_sql: str) -> re.Pattern | None:
    """A cell's SQL with f-string fields ({expr}) as wildcards, to match the statement as it ran."""
    if "{" not in cell_sql:
        return None
    parts = re.split(r"\{[^{}]*\}", norm_sql(cell_sql))
    return re.compile(".*?".join(re.escape(p) for p in parts) + "$", re.S)


def short_title(text: str) -> str:
    first = re.split(r"(?<=\.)\s", text.strip(), maxsplit=1)[0]  # sentences, not "Cause: ..." labels
    if len(first) <= TITLE_CHARS:
        return first.rstrip(".;:")
    cut = first[:TITLE_CHARS].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "…"


class _Clock:
    def __init__(self, t0: datetime | None):
        self.t0 = t0

    def __call__(self, t: datetime | None) -> float | None:
        if t is None or self.t0 is None:
            return None
        return round((t - self.t0).total_seconds(), 3)


def _index(sessions: list[Session]) -> dict[str, list[tuple[str, re.Pattern | None, Cell]]]:
    idx: dict[str, list[tuple[str, re.Pattern | None, Cell]]] = {}
    for s in sessions:
        for c in s.cells:
            for q in c.sql:
                idx.setdefault(s.name, []).append((norm_sql(q), _pattern(q), c))
    return idx


def _match(idx, session: str, sql: str) -> Cell | None:
    want = norm_sql(sql)
    pools = [idx.get(session, [])] + [v for k, v in idx.items() if k != session]
    for pool in pools:
        for text, _, cell in pool:
            if text == want:
                return cell
    for pool in pools:
        for _, pattern, cell in pool:
            if pattern and pattern.match(want):
                return cell
    return None


def build(inv: Investigation) -> dict[str, Any]:
    sessions = list(inv.root.walk())
    times = [t for s in sessions for t in (s.started, s.ended) if t] + \
            [t for s in sessions for c in s.cells for t in (c.started, c.ended) if t]
    t0 = min(times) if times else inv.summary.started
    clock = _Clock(t0)
    end = max(times) if times else inv.summary.ended
    by_name = {s.name: i for i, s in enumerate(sessions)}

    out_sessions, queries, events = [], [], []
    for i, s in enumerate(sessions):
        cells = []
        for j, c in enumerate(s.cells):
            cells.append({"i": j, "start": clock(c.started), "end": clock(c.ended), "ok": c.ok,
                          "duration_ms": c.duration_ms, "sql": bool(c.sql)})
            if c.sql:
                queries.append({"session": i, "cell": j, "start": clock(c.started), "end": clock(c.ended),
                                "ok": c.ok, "duration_ms": c.duration_ms, "sql": c.sql, "rows": None,
                                "result": c.result[:RESULT_PREVIEW], "truncated": len(c.result) > RESULT_PREVIEW})
        for m in s.messages:
            if m.kind == "text" and s.role == "root":
                events.append({"kind": "thought", "session": i, "at": clock(m.at), "text": m.text[:MESSAGE_CHARS],
                               "truncated": len(m.text) > MESSAGE_CHARS})
            elif m.kind in ("report", "notice") and s.role == "root":
                events.append({"kind": m.kind, "session": by_name.get(m.sender, i), "at": clock(m.at),
                               "sender": m.sender, "text": m.text[:400]})
        out_sessions.append({
            "index": i, "id": s.id, "name": s.name, "role": s.role, "status": s.status, "model": s.model,
            "start": clock(s.started), "end": clock(s.ended), "tokens": s.tokens,
            "queries": sum(1 for c in s.cells if c.sql), "cells": cells,
        })
    for s in out_sessions:
        if s["role"] == "child" and s["start"] is not None:
            events.append({"kind": "spawn", "session": s["index"], "at": s["start"], "text": s["name"]})

    idx = _index(sessions)
    hypotheses = []
    contract = inv.findings or {}
    for kind, key, items in (("supported", "claim", contract.get("findings") or []),
                             ("ruled_out", "explanation", contract.get("ruled_out") or [])):
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get(key), str):
                continue
            evidence = []
            for e in item.get("evidence") or []:
                if not isinstance(e, dict) or not isinstance(e.get("sql"), str):
                    continue
                cell = _match(idx, str(e.get("session", "")), e["sql"])
                at = clock(cell.ended or cell.started) if cell else None
                evidence.append({"session": e.get("session"), "sql": e["sql"], "rows": e.get("rows"),
                                 "elapsed_ms": e.get("elapsed_ms"), "at": at})
                if cell is not None:
                    for q in queries:
                        if q["session"] == by_name.get(e.get("session"), -1) and sessions[q["session"]].cells[q["cell"]] is cell:
                            q["rows"] = e.get("rows")
            ats = [e["at"] for e in evidence if e["at"] is not None]
            hypotheses.append({
                "kind": kind, "title": short_title(item[key]), "text": item[key],
                "reason": item.get("reason"), "confidence": item.get("confidence"),
                "evidence": evidence,
                "first_at": min(ats) if ats else clock(end),
                "resolved_at": max(ats) if ats else clock(end),
            })

    queries.sort(key=lambda q: (q["start"] is None, q["start"] or 0))
    events.sort(key=lambda e: (e["at"] is None, e["at"] or 0))
    return {
        "run_id": inv.summary.run_id, "status": inv.summary.status,
        "started": t0.isoformat() if t0 else None, "duration_s": clock(end),
        "replayable": bool(times) and sum(1 for q in queries if q["start"] is not None) > 0,
        "sessions": out_sessions, "queries": queries, "events": events, "hypotheses": hypotheses,
    }
