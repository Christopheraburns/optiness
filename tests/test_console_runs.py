"""H1: the console's file-backed source reads real runs (vendored from E6/E8
smoke runs under tests/fixtures/console) into the console model."""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.console.model import ABANDONED, DONE, ENDED, RUNNING, STALLED  # noqa: E402
from services.console.runs import FileRunSource, evidence_in, read_output, sql_in_code  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "console"
RUNS = FIX / "runs"
AGENT = FIX / "prime-agent"
LATER = lambda: 4_000_000_000.0  # noqa: E731  long after every fixture: nothing is fresh


def src(**kw) -> FileRunSource:
    kw.setdefault("now", LATER)
    return FileRunSource(RUNS, AGENT, **kw)


def test_lists_newest_first_with_status():
    got = [(s.run_id, s.status, s.children, s.has_findings) for s in src().list_investigations()]
    assert got == [
        ("inv-20261021-201822", DONE, 3, True),
        ("inv-20261021-194859", ENDED, 0, False),
        ("inv-20261021-184608", DONE, 2, True),
    ]


def test_question_from_task():
    s = src().get_investigation("inv-20261021-184608").summary
    assert s.question.startswith("For payment_transactions, compute attempts and decline rate")


def test_session_tree_root_to_children():
    inv = src().get_investigation("inv-20261021-201822")
    root = inv.root
    assert (root.role, root.model, root.id) == ("root", "mistral/mistral-large-latest", "b47b5389-6e2b-4873-9731-1b24b5bcb766")
    assert [(c.id, c.name, c.model, c.status) for c in root.children] == [
        ("sub-89ce82af", "time-slice-1-baseline", "mistral/ministral-8b-latest", "completed"),
        ("sub-4b3fb71d", "time-slice-2-anomaly", "mistral/ministral-8b-latest", "completed"),
        ("sub-1abc4839", "time-slice-3-post-anomaly", "mistral/ministral-8b-latest", "completed"),
    ]
    assert [c.output.name for c in root.children] == ["slice-1.json", "slice-2.json", "slice-3.json"]
    assert inv.unattributed == []


def test_elapsed_and_tokens_from_transcripts():
    root = src().get_investigation("inv-20261021-201822").root
    assert root.elapsed_s == pytest.approx(180.574)
    assert [round(c.elapsed_s, 3) for c in root.children] == [93.157, 43.273, 66.643]
    assert all(c.tokens and c.tokens > 100_000 for c in root.children)
    assert root.tokens == 297_645


def test_root_cells_come_from_events():
    root = src().get_investigation("inv-20261021-201822").root
    assert len(root.cells) == 7
    assert "partition-investigator" in root.cells[0].code
    # the root's rlm.list_subagents() typo raised AttributeError
    assert [c.ok for c in root.cells].count(False) == 1


def test_child_cells_sql_and_failures():
    child = src().get_investigation("inv-20261021-201822").root.children[0]
    assert len(child.cells) == 25
    first_sql = next(c for c in child.cells if c.sql)
    assert "payment_transactions" in first_sql.sql[0]
    # Ministral 8B tried COUNT(*) FILTER (...) on Impala several times; caught or not, those are failures
    assert sum(not c.ok for c in child.cells) >= 4
    # it also called a tool that does not exist, four times
    bogus = [c for c in child.cells if c.code.startswith("# tool call: agent_message")]
    assert len(bogus) == 4 and not any(c.ok for c in bogus)
    assert all(c.duration_ms is not None for c in child.cells if c not in bogus)


def test_cited_evidence_and_rows():
    child = src().get_investigation("inv-20261021-201822").root.children[1]
    ev = child.output.evidence
    assert len(ev) == 4
    assert ev[0].path == "decline_rate.target.evidence"
    assert (ev[0].row_count, ev[0].elapsed_ms, ev[0].truncated) == (1, 279, False)
    assert "transaction_ts BETWEEN '2026-09-19' AND '2026-09-20'" in ev[0].sql
    assert child.rows == sum(e.row_count for e in ev)


def test_abandoned_child_in_finished_run():
    inv = src().get_investigation("inv-20261021-184608")
    assert inv.summary.status == DONE  # findings.md written, stream cut off
    assert [(c.name, c.status) for c in inv.root.children] == [
        ("half_1_investigator", ABANDONED), ("half_2_investigator", "completed"),
    ]
    assert [c.output.name for c in inv.root.children] == ["half_1.json", "half_2.json"]


def test_run_without_children_or_findings():
    inv = src().get_investigation("inv-20261021-194859")
    assert inv.root.children == [] and inv.findings_md == ""
    assert inv.root_reply  # the root answered in text without calling tools
    assert inv.summary.started is not None  # from the run id


def test_without_agent_dir_children_come_from_events_only():
    inv = FileRunSource(RUNS, None, now=LATER).get_investigation("inv-20261021-201822")
    assert [c.status for c in inv.root.children] == ["done", "done", "done"]
    assert all(c.output is None and c.elapsed_s is None for c in inv.root.children)
    assert sorted(o.name for o in inv.unattributed) == ["slice-1.json", "slice-2.json", "slice-3.json"]


def live_copy(tmp_path: Path) -> Path:
    """201822 as it looked mid-run: no findings, no end of turn, a partial last line."""
    runs = tmp_path / "runs"
    run = runs / "inv-20261021-201822"
    shutil.copytree(RUNS / run.name, run)
    (run / "findings.md").unlink()
    lines = (run / "events.jsonl").read_text().splitlines()
    keep = lines[:lines.index(next(l for l in lines if '"terminalQuiescence"' in l))]

    def child_done(line: str) -> bool:
        subs = json.loads(line)["params"]["update"].get("_meta", {}).get("ai.primeintellect.prime-agent", {}).get("subagents", [])
        return any(s["status"] != "running" for s in subs)

    keep = [l for l in keep if '"subagents"' not in l or not child_done(l)]  # children not reported yet
    (run / "events.jsonl").write_text("\n".join(keep) + '\n{"jsonrpc": "2.0", "meth')
    return runs


def test_running_then_stalled(tmp_path):
    runs = live_copy(tmp_path)
    inv = FileRunSource(runs, None, now=time.time).get_investigation("inv-20261021-201822")
    assert inv.summary.status == RUNNING
    assert inv.root.ended is None
    assert [c.status for c in inv.root.children] == ["running"] * 3

    later = FileRunSource(runs, None, now=lambda: time.time() + 3600).get_investigation("inv-20261021-201822")
    assert later.summary.status == STALLED
    assert [c.status for c in later.root.children] == [ABANDONED] * 3


def test_cache_follows_file_changes(tmp_path):
    runs = live_copy(tmp_path)
    s = FileRunSource(runs, None, now=time.time)
    first = s.get_investigation("inv-20261021-201822")
    assert s.get_investigation("inv-20261021-201822") is first
    run = runs / "inv-20261021-201822"
    (run / "findings.md").write_text("# Findings\n")
    with (run / "events.jsonl").open("a") as f:
        f.write('\n{"jsonrpc": "2.0", "id": 3, "result": {"stopReason": "end_turn"}}\n')
    again = s.get_investigation("inv-20261021-201822")
    assert again is not first and again.summary.status == DONE and again.findings_md == "# Findings\n"


def test_unknown_or_bad_run_id():
    assert src().get_investigation("inv-20990101-000000") is None
    assert src().get_investigation("../runs") is None


def test_invalid_json_output_keeps_cited_sql(tmp_path):
    p = tmp_path / "slice-9.json"
    p.write_text('{"a": {"evidence": {"sql": "SELECT 1\\nFROM t", "row_count": 1}}, "b": oops}')
    out = read_output(p)
    assert out.parse_error and out.data is None
    assert [e.sql for e in out.evidence] == ["SELECT 1\nFROM t"]


def test_evidence_shapes():
    data = {"query": "SELECT 2", "items": [{"sql": "WITH x AS (SELECT 1) SELECT * FROM x", "rows": 3}], "note": "SELECT is a word"}
    got = [(e.sql, e.row_count, e.path) for e in evidence_in(data)]
    assert got == [("SELECT 2", None, "(top level)"), ("WITH x AS (SELECT 1) SELECT * FROM x", 3, "items[0]")]


def test_sql_in_code():
    code = (
        'day = "2026-09-19"\n'
        'q = f"""\nSELECT COUNT(*) FROM payment_transactions\nWHERE transaction_ts >= \'{day}\'\n"""\n'
        'df = await lakehouse.query("SHOW TABLES")\n'
        'print("selected rows")\n'
    )
    assert sql_in_code(code) == [
        "SELECT COUNT(*) FROM payment_transactions\nWHERE transaction_ts >= '{day}'",
        "SHOW TABLES",
    ]
    assert sql_in_code('x = """SELECT 1"""\nthis is not python(') == ["SELECT 1"]
