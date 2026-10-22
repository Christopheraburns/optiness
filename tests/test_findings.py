"""Findings output contract (E10)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.common.findings import FindingsError, parse  # noqa: E402

EV = {"session": "slice-a", "sql": "SELECT COUNT(*) FROM t", "rows": 1, "elapsed_ms": 205}
DOC = {"findings": [{"claim": "Declines rose.", "confidence": "High", "evidence": [EV]}],
       "ruled_out": [{"explanation": "Weekend volume", "reason": "Rates flat on weekends.",
                      "evidence": [dict(EV, session="root")]}]}


def report(doc, prose="# Findings\n\nSome prose with an example:\n\n```json\n{\"not\": \"this one\"}\n```\n\n") -> str:
    return prose + "```json\n" + json.dumps(doc, indent=1) + "\n```\n"


def test_parses_the_last_block_and_normalizes():
    out = parse(report(DOC))
    assert out["findings"][0]["confidence"] == "high"
    assert out["findings"][0]["evidence"] == [EV]
    assert out["ruled_out"][0]["evidence"][0]["session"] == "root"


def test_ruled_out_is_optional_and_whole_floats_are_accepted():
    doc = {"findings": [{"claim": "x", "confidence": "low", "evidence": [dict(EV, rows=4.0)]}]}
    out = parse(report(doc))
    assert out["ruled_out"] == [] and out["findings"][0]["evidence"][0]["rows"] == 4


@pytest.mark.parametrize("text, message", [
    ("# Findings\n\nno block", "no ```json block"),
    ("```json\n{broken\n```", "not valid JSON"),
    ("```json\n[1]\n```", "not an object"),
])
def test_rejects_missing_or_broken_blocks(text, message):
    with pytest.raises(FindingsError, match=message):
        parse(text)


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d.update(findings=[]), "findings: must be a non-empty list"),
    (lambda d: d["findings"][0].update(claim=" "), r"findings\[0\].claim"),
    (lambda d: d["findings"][0].update(confidence="certain"), r"findings\[0\].confidence"),
    (lambda d: d["findings"][0].update(evidence=[]), r"findings\[0\]: evidence"),
    (lambda d: d["findings"][0]["evidence"][0].pop("sql"), r"evidence\[0\].sql"),
    (lambda d: d["findings"][0]["evidence"][0].update(rows=-1), r"evidence\[0\].rows"),
    (lambda d: d["findings"][0]["evidence"][0].update(elapsed_ms=True), r"evidence\[0\].elapsed_ms"),
    (lambda d: d["findings"][0]["evidence"][0].update(rows=2.5), r"evidence\[0\].rows"),
    (lambda d: d["ruled_out"][0].update(reason=""), r"ruled_out\[0\].reason"),
    (lambda d: d["ruled_out"][0].update(evidence=None), r"ruled_out\[0\]: evidence"),
])
def test_rejects_contract_violations(mutate, message):
    doc = json.loads(json.dumps(DOC))
    mutate(doc)
    with pytest.raises(FindingsError, match=message):
        parse(report(doc))


def test_report_text_drops_only_the_trailing_contract_block():
    from services.common.findings import report_text
    text = "# Findings\n\nAn example:\n\n```json\n{\"x\": 1}\n```\n\nMore prose.\n\n```json\n{\"findings\": []}\n```\n"
    assert report_text(text) == "# Findings\n\nAn example:\n\n```json\n{\"x\": 1}\n```\n\nMore prose."
    assert report_text("no block here\n") == "no block here"
    assert report_text("") == ""
