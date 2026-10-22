"""The findings output contract (BURNDOWN E10): what the root's report must end with.

The root writes ``findings.md`` for people and ends it with one fenced ``json``
block that machines read (the bridge's ``get_findings``, F5):

    {"findings": [{"claim": str, "confidence": "high" | "medium" | "low",
                   "evidence": [{"session": str, "sql": str, "rows": int, "elapsed_ms": int}]}],
     "ruled_out": [{"explanation": str, "reason": str, "evidence": [...]}]}

``session`` is "root" or the child's session name. ``rows`` and ``elapsed_ms``
are what ``lakehouse.evidence()`` reported for that statement. Every finding
and every ruled-out explanation needs at least one piece of evidence.
``ruled_out`` may be empty.
"""
from __future__ import annotations

import json
import re

CONFIDENCE = ("high", "medium", "low")
_FENCE = re.compile(r"```json[ \t]*\n(.*?)\n```", re.DOTALL)


class FindingsError(ValueError):
    """The report has no contract block, or the block breaks the contract."""


def report_text(text: str) -> str:
    """The written report without its trailing contract block (the structured findings carry it)."""
    cut = [m.start() for m in re.finditer(r"```json[ \t]*\n", text or "")]
    return ((text or "")[:cut[-1]] if cut else (text or "")).rstrip()


def extract(text: str) -> dict:
    """The last fenced ``json`` block in the report, parsed but not validated."""
    blocks = _FENCE.findall(text or "")
    if not blocks:
        raise FindingsError("no ```json block in the report")
    try:
        doc = json.loads(blocks[-1])
    except json.JSONDecodeError as e:
        raise FindingsError(f"the last ```json block is not valid JSON: {e}") from None
    if not isinstance(doc, dict):
        raise FindingsError("the last ```json block is not an object")
    return doc


def _evidence(items, where: str) -> list[dict]:
    if not isinstance(items, list) or not items:
        raise FindingsError(f"{where}: evidence must be a non-empty list")
    out = []
    for i, ev in enumerate(items):
        at = f"{where}.evidence[{i}]"
        if not isinstance(ev, dict):
            raise FindingsError(f"{at}: not an object")
        session, sql = ev.get("session"), ev.get("sql")
        if not isinstance(session, str) or not session.strip():
            raise FindingsError(f"{at}.session: missing")
        if not isinstance(sql, str) or not sql.strip():
            raise FindingsError(f"{at}.sql: missing")
        for key in ("rows", "elapsed_ms"):
            value = ev.get(key)
            # bool is an int subclass; JSON floats like 12.0 are accepted as whole numbers
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or value != int(value):
                raise FindingsError(f"{at}.{key}: must be a non-negative integer")
        out.append({"session": session.strip(), "sql": sql.strip(),
                    "rows": int(ev["rows"]), "elapsed_ms": int(ev["elapsed_ms"])})
    return out


def validate(doc: dict) -> dict:
    """The contract in normalized form, or FindingsError naming the first problem."""
    findings = doc.get("findings")
    if not isinstance(findings, list) or not findings:
        raise FindingsError("findings: must be a non-empty list")
    out = {"findings": [], "ruled_out": []}
    for i, f in enumerate(findings):
        at = f"findings[{i}]"
        if not isinstance(f, dict):
            raise FindingsError(f"{at}: not an object")
        claim, confidence = f.get("claim"), str(f.get("confidence", "")).strip().lower()
        if not isinstance(claim, str) or not claim.strip():
            raise FindingsError(f"{at}.claim: missing")
        if confidence not in CONFIDENCE:
            raise FindingsError(f"{at}.confidence: must be one of {', '.join(CONFIDENCE)}")
        out["findings"].append({"claim": claim.strip(), "confidence": confidence,
                                "evidence": _evidence(f.get("evidence"), at)})
    ruled_out = doc.get("ruled_out", [])
    if not isinstance(ruled_out, list):
        raise FindingsError("ruled_out: must be a list")
    for i, r in enumerate(ruled_out):
        at = f"ruled_out[{i}]"
        if not isinstance(r, dict):
            raise FindingsError(f"{at}: not an object")
        explanation, reason = r.get("explanation"), r.get("reason")
        if not isinstance(explanation, str) or not explanation.strip():
            raise FindingsError(f"{at}.explanation: missing")
        if not isinstance(reason, str) or not reason.strip():
            raise FindingsError(f"{at}.reason: missing")
        out["ruled_out"].append({"explanation": explanation.strip(), "reason": reason.strip(),
                                 "evidence": _evidence(r.get("evidence"), at)})
    return out


def parse(text: str) -> dict:
    """Extract and validate the contract block from a findings report."""
    return validate(extract(text))
