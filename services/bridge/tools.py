"""The MCP tools Mistral Studio's agent calls (BURNDOWN F4).

Investigations take minutes, so the shape is start, poll, fetch: `start_investigation`
returns an id at once; `get_investigation` reports progress; `get_findings` returns the
E10 contract (claims with confidence and the SQL behind them) once the run is done.
Every response carries `next_step`, a sentence telling the calling model what to do next,
because the caller is an LLM deciding when to poll.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Callable, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

from services.bridge.runner import Runner
from services.bridge.store import CANCELLED, DONE, QUEUED, RUNNING, TERMINAL
from services.common.env import settings
from services.common.findings import report_text

REPORT_LIMIT = 12_000
RECENT_EVENTS = 8
POLL_HINT = "Poll get_investigation again in about 60 seconds; do not start a duplicate."

InvestigationId = Annotated[str, Field(description="Investigation id from start_investigation, e.g. inv-20261021-213134",
                                       pattern=r"^inv-\d{8}-\d{6}$")]
Status = Literal["queued", "running", "done", "failed", "timeout", "cancelled"]


# ------------------------------------------------------------------ outputs
class Evidence(BaseModel):
    session: str = Field(description="'root' or the subagent that ran the statement")
    sql: str
    rows: int = Field(description="Rows the statement returned")
    elapsed_ms: int


class Finding(BaseModel):
    claim: str
    confidence: Literal["high", "medium", "low"]
    evidence: list[Evidence]


class RuledOut(BaseModel):
    explanation: str
    reason: str
    evidence: list[Evidence]


class Summary(BaseModel):
    id: str
    status: Status
    question: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    elapsed_s: float | None = Field(None, description="Seconds running so far, or in total once finished")
    outcome: str | None = None
    error: str | None = None
    parent_id: str | None = Field(None, description="The investigation this one follows up")


class Event(BaseModel):
    at: str
    kind: str
    session: str | None = None
    detail: str


class Progress(BaseModel):
    subagents_spawned: int
    subagents_reported: int
    subagents_reprompted: int = Field(description="Re-prompts sent to subagents that stopped without reporting")
    subagents_failed: int
    root_tool_calls: int
    latest_root_message: str | None = None
    recent_events: list[Event]


class Started(BaseModel):
    id: str
    status: Status
    queue_position: int | None = Field(None, description="1 = next to run; null once running")
    next_step: str


class InvestigationView(Summary):
    queue_position: int | None = None
    progress: Progress
    next_step: str


class FindingsView(BaseModel):
    id: str
    status: Status
    ready: bool = Field(description="True when findings are available")
    question: str
    findings: list[Finding] = []
    ruled_out: list[RuledOut] = []
    report_markdown: str | None = Field(None, description="The full written report, without its JSON block")
    next_step: str


class InvestigationList(BaseModel):
    investigations: list[Summary]
    next_step: str


# ------------------------------------------------------------------ helpers
def _elapsed(inv: dict) -> float | None:
    if not inv.get("started_at"):
        return None
    start = datetime.fromisoformat(inv["started_at"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(inv["finished_at"].replace("Z", "+00:00")) if inv.get("finished_at") \
        else datetime.now(start.tzinfo)
    return round((end - start).total_seconds(), 1)


def _summary(inv: dict) -> Summary:
    keys = Summary.model_fields.keys() - {"elapsed_s"}
    return Summary(**{k: inv.get(k) for k in keys}, elapsed_s=_elapsed(inv))


def _detail(e: dict) -> str:
    d = e["data"]
    if e["kind"] == "tool_call":
        return next((ln.strip() for ln in d.get("code", "").splitlines() if ln.strip()), "")[:160]
    if e["kind"] == "root_message":
        return d.get("text", "")[:300]
    return ", ".join(f"{k}={v}" for k, v in d.items() if k not in ("id", "files"))[:200]


def _report(run_dir: str | None) -> str | None:
    path = Path(run_dir or "") / "findings.md"
    if not run_dir or not path.is_file():
        return None
    text = report_text(path.read_text())
    return text if len(text) <= REPORT_LIMIT else text[:REPORT_LIMIT] + "\n\n[report truncated]"


def _next_step(inv: dict, queue_position: int | None) -> str:
    status = inv["status"]
    if status == QUEUED:
        return f"Queued (position {queue_position}). {POLL_HINT}"
    if status == RUNNING:
        minutes = (_elapsed(inv) or 0) / 60
        return (f"Running for {minutes:.0f} min; full investigations usually take 5-25 min. {POLL_HINT} "
                "Tell the user it is in progress.")
    if status == DONE:
        return f"Done. Call get_findings('{inv['id']}') and report the claims with their evidence."
    if status == CANCELLED:
        return "Cancelled. Start a new investigation if the user still wants an answer."
    return (f"Ended without findings ({inv.get('outcome')}). Tell the user, and offer to start a new "
            "investigation, possibly with a narrower question.")


# ------------------------------------------------------------------ tools
def register(mcp: FastMCP, get_runner: Callable[[], Runner]) -> None:
    """Add the investigation tools to `mcp`. `get_runner` returns the live Runner."""

    def runner() -> Runner:
        r = get_runner()
        if r is None:
            raise ToolError("the bridge is still starting; retry in a few seconds")
        return r

    def load(inv_id: str) -> dict:
        inv = runner().store.get_investigation(inv_id)
        if inv is None:
            raise ToolError(f"no investigation {inv_id}; list_investigations shows the known ids")
        return inv

    @mcp.tool(annotations={"readOnlyHint": False, "openWorldHint": False})
    def start_investigation(
        question: Annotated[str, Field(description="The business question in plain language, e.g. 'Why did card "
                                                   "decline rates rise in September?'", min_length=5)],
        scope: Annotated[str | None, Field(description="Optional limits from the user: tables, date range, segment")]
        = None,
        subagents: Annotated[int, Field(description="Parallel subagents for the investigation (default 3)", ge=1)]
        = 3,
    ) -> Started:
        """Start a root-cause investigation over the bank's payments lakehouse.

        A Prime Agent on Cloudera AI plans from the data catalog, splits the work across
        subagents that run read-only SQL, and writes findings that cite the SQL and row counts
        behind every number. Returns immediately with an id; the investigation takes minutes.
        Then poll get_investigation(id) about once a minute and call get_findings(id) when done.
        """
        if subagents > settings().max_children:
            raise ToolError(f"subagents must be at most {settings().max_children}")
        try:
            inv = runner().submit(question, scope={"text": scope} if scope else None, children=subagents)
        except ValueError as e:
            raise ToolError(str(e)) from None
        pos = runner().queue_position(inv["id"])
        inv = runner().store.get_investigation(inv["id"])
        return Started(id=inv["id"], status=inv["status"], queue_position=pos,
                       next_step=f"Started investigation {inv['id']}. Tell the user it is under way and takes "
                                 f"several minutes. {POLL_HINT}")

    def view(id: str) -> InvestigationView:
        inv = load(id)
        events = runner().store.events(id, limit=5000)
        count = lambda kind: sum(1 for e in events if e["kind"] == kind)  # noqa: E731
        messages = [e for e in events if e["kind"] == "root_message"]
        pos = runner().queue_position(id)
        progress = Progress(
            subagents_spawned=count("child_spawned"), subagents_reported=count("child_reported"),
            subagents_reprompted=count("child_reprompted"), subagents_failed=count("child_failed"),
            root_tool_calls=count("tool_call"),
            latest_root_message=messages[-1]["data"]["text"][:600] if messages else None,
            recent_events=[Event(at=e["ts"], kind=e["kind"], session=e["session"], detail=_detail(e))
                           for e in events[-RECENT_EVENTS:]])
        return InvestigationView(**_summary(inv).model_dump(), queue_position=pos, progress=progress,
                                 next_step=_next_step(inv, pos))

    @mcp.tool(annotations={"readOnlyHint": True})
    def get_investigation(id: InvestigationId) -> InvestigationView:
        """Status and progress of one investigation: subagents spawned and reported, the root agent's
        latest message, and recent events. Poll about once a minute while it is queued or running."""
        return view(id)

    @mcp.tool(annotations={"readOnlyHint": True})
    def get_findings(id: InvestigationId) -> FindingsView:
        """Findings of a finished investigation: each claim with its confidence and the SQL evidence
        (statement, rows, timing, and which agent ran it), the explanations that were ruled out and
        why, and the written report. If it is not finished yet, says so."""
        inv = load(id)
        found = runner().store.get_findings(id)
        ready = inv["status"] == DONE and bool(found["findings"])
        return FindingsView(
            id=id, status=inv["status"], ready=ready, question=inv["question"],
            findings=found["findings"], ruled_out=found["ruled_out"],
            report_markdown=_report(inv.get("run_dir")) if ready else None,
            next_step=("Report the findings to the user with their evidence; mention what was ruled out. "
                       f"For a related question, use ask_followup('{id}', ...).") if ready
            else _next_step(inv, runner().queue_position(id)))

    @mcp.tool(annotations={"readOnlyHint": False, "openWorldHint": False})
    def ask_followup(
        id: InvestigationId,
        question: Annotated[str, Field(description="The follow-up question", min_length=5)],
        subagents: Annotated[int, Field(description="Parallel subagents (default 2)", ge=1)] = 2,
    ) -> Started:
        """Start a new investigation that builds on a finished one: it receives the earlier findings as
        prior results to re-check, and is linked to it. Use for drill-downs such as 'which merchants
        were hit hardest?'. Poll and fetch it like any investigation."""
        parent = load(id)
        if parent["status"] not in TERMINAL:
            raise ToolError(f"{id} is still {parent['status']}; wait for it to finish before following up")
        if subagents > settings().max_children:
            raise ToolError(f"subagents must be at most {settings().max_children}")
        inv = runner().submit(question, children=subagents, parent_id=id)
        pos = runner().queue_position(inv["id"])
        inv = runner().store.get_investigation(inv["id"])
        return Started(id=inv["id"], status=inv["status"], queue_position=pos,
                       next_step=f"Started follow-up {inv['id']} to {id}. {POLL_HINT}")

    @mcp.tool(annotations={"readOnlyHint": True})
    def list_investigations(
        limit: Annotated[int, Field(description="How many, newest first", ge=1, le=100)] = 10,
        status: Annotated[Status | None, Field(description="Only investigations in this status")] = None,
    ) -> InvestigationList:
        """Recent investigations, newest first, with status and timing. Use it to find an id or to
        check whether a question was already investigated before starting a new one."""
        rows = runner().store.list_investigations(limit=limit, status=status)
        return InvestigationList(investigations=[_summary(i) for i in rows],
                                 next_step="Use get_findings(id) for a done investigation, get_investigation(id) "
                                           "for one in progress.")

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True})
    def cancel_investigation(id: InvestigationId) -> InvestigationView:
        """Cancel a queued or running investigation. A running one stops within a few seconds and
        keeps nothing it had not finished. Only when the user asks to stop it."""
        inv = load(id)
        if inv["status"] in TERMINAL:
            raise ToolError(f"{id} already ended ({inv['status']})")
        runner().cancel(id)
        return view(id)
