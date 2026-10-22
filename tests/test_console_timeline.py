"""P2 (H6-H8): cell times and messages from transcripts, and the run timeline over the fixture runs."""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.console.model import Cell, Investigation, InvestigationSummary, Session  # noqa: E402
from services.console.runs import FileRunSource  # noqa: E402
from services.console.timeline import build, short_title  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "console"
LATER = lambda: 4e9  # noqa: E731


def source(runs: Path = FIX / "runs") -> FileRunSource:
    return FileRunSource(runs, FIX / "prime-agent", now=LATER)


def test_transcripts_time_every_cell_and_keep_messages():
    inv = source().get_investigation("inv-20261021-201822")
    for s in inv.root.walk():
        assert s.cells and all(c.started and c.ended and c.ended >= c.started for c in s.cells), s.name
    kinds = {m.kind for m in inv.root.messages}
    assert {"text", "report", "notice"} <= kinds
    reports = [m for m in inv.root.messages if m.kind == "report"]
    assert all(m.sender.startswith("time-slice-") and not m.text.startswith("[") for m in reports)


def test_timeline_orders_activity_and_starts_at_zero():
    t = build(source().get_investigation("inv-20261021-201822"))
    assert t["replayable"] and t["duration_s"] > 0
    starts = [q["start"] for q in t["queries"]]
    assert starts == sorted(starts) and min(starts) >= 0
    assert [s["role"] for s in t["sessions"]] == ["root", "child", "child", "child"]
    spawns = [e for e in t["events"] if e["kind"] == "spawn"]
    assert len(spawns) == 3 and all(e["at"] is not None for e in spawns)
    assert any(e["kind"] == "report" and t["sessions"][e["session"]]["name"].startswith("time-slice-") for e in t["events"])
    assert t["hypotheses"] == []  # this fixture predates findings.json


def test_hypotheses_appear_with_their_first_evidence_and_resolve_with_the_last(tmp_path):
    runs = tmp_path / "runs"
    shutil.copytree(FIX / "runs" / "inv-20261021-201822", runs / "inv-20261021-201822")
    inv = source(runs).get_investigation("inv-20261021-201822")
    child = inv.root.children[0]
    sql_cells = [c for c in child.cells if c.sql]
    early, late = sql_cells[0], sql_cells[-1]
    contract = {
        "findings": [{"claim": "Rates rose in the window. Details follow.", "confidence": "high", "evidence": [
            {"session": child.name, "sql": "  " + early.sql[0].replace("\n", "  \n ") + ";", "rows": 3, "elapsed_ms": 9},
            {"session": child.name, "sql": late.sql[0], "rows": 5, "elapsed_ms": 9}]}],
        "ruled_out": [{"explanation": "Weekend dips", "reason": "Flat", "evidence": [
            {"session": "root", "sql": "SELECT 'never ran'", "rows": 1, "elapsed_ms": 1}]}],
    }
    (runs / "inv-20261021-201822" / "findings.json").write_text(json.dumps(contract))
    t = build(source(runs).get_investigation("inv-20261021-201822"))
    supported, ruled = t["hypotheses"]
    assert supported["title"] == "Rates rose in the window" and supported["kind"] == "supported"
    assert all(e["at"] is not None for e in supported["evidence"])  # whitespace and ';' differences still match
    assert supported["first_at"] < supported["resolved_at"] <= t["duration_s"]
    assert ruled["kind"] == "ruled_out" and ruled["first_at"] == ruled["resolved_at"] == t["duration_s"]  # unmatched: at the end
    matched = [q for q in t["queries"] if q["rows"] is not None]
    assert {q["rows"] for q in matched} == {3, 5}


def test_fstring_sql_matches_the_statement_as_run():
    t0 = datetime(2026, 10, 22, tzinfo=timezone.utc)
    cell = Cell(code="", sql=["SELECT COUNT(*) FROM t WHERE day >= '{start}' AND day < '{end}'"],
                started=t0 + timedelta(seconds=5), ended=t0 + timedelta(seconds=7))
    root = Session(id="r", name="root", role="root", started=t0, ended=t0 + timedelta(seconds=30), cells=[cell])
    inv = Investigation(summary=InvestigationSummary("inv-20261022-000000", "q", "done", t0, t0, 0, True), root=root,
                        findings={"findings": [{"claim": "x", "confidence": "low", "evidence": [
                            {"session": "root", "sql": "select count(*) from t where day >= '2026-09-11' and day < '2026-10-03'"}]}]})
    h = build(inv)["hypotheses"][0]
    assert h["evidence"][0]["at"] == 7.0


def test_short_titles_keep_label_prefixes():
    assert short_title("Cause: config X on family Y. More.") == "Cause: config X on family Y"
    long = "word " * 60
    assert short_title(long).endswith("…") and len(short_title(long)) <= 112
