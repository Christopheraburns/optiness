"""What the console shows, independent of where it came from.

`FileRunSource` (runs.py) builds these from run directories today; the bridge's
SQLite job store (BURNDOWN F2) is meant to build the same objects later, so the
page never reads files or tables itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

# Investigation status values.
RUNNING = "running"        # events still arriving
DONE = "done"              # findings.md written
ENDED = "ended"            # turn ended without findings
STALLED = "stalled"        # no events for a while and no end of turn

# Session status values (child statuses come straight from prime-agent:
# running / done / completed / failed ...; ABANDONED is ours).
ABANDONED = "abandoned"    # still "running" in a run that is no longer running


@dataclass
class Evidence:
    """One SQL citation a child wrote into its output file."""
    sql: str
    row_count: int | None = None
    elapsed_ms: int | None = None
    truncated: bool | None = None
    path: str = ""             # where in the JSON it was found, e.g. decline_rate.evidence


@dataclass
class OutputFile:
    """A JSON file a child wrote into the run directory. Free-form: the child
    chooses the shape, and sometimes writes invalid JSON."""
    name: str
    text: str
    data: Any = None
    parse_error: str = ""
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class Cell:
    """One code cell a session ran and what came back."""
    code: str
    result: str = ""
    ok: bool = True
    duration_ms: int | None = None
    sql: list[str] = field(default_factory=list)
    started: datetime | None = None   # when the model issued the call (transcript)
    ended: datetime | None = None     # when its result came back


@dataclass
class Message:
    """Something a session said or received, in time order (from its transcript)."""
    at: datetime | None
    kind: str                  # "text" (its own reasoning), "report" (a child's message to it), "notice" (child exited)
    text: str
    sender: str = ""           # for report/notice: the child's session name


@dataclass
class Session:
    id: str
    name: str
    role: str                  # "root" or "child"
    model: str = ""
    status: str = ""
    started: datetime | None = None
    ended: datetime | None = None
    tokens: int | None = None
    cells: list[Cell] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)
    output: OutputFile | None = None
    children: list[Session] = field(default_factory=list)

    @property
    def elapsed_s(self) -> float | None:
        if self.started and self.ended:
            return (self.ended - self.started).total_seconds()
        return None

    @property
    def rows(self) -> int | None:
        """Rows behind the child's cited evidence."""
        counts = [e.row_count for e in (self.output.evidence if self.output else []) if e.row_count is not None]
        return sum(counts) if counts else None

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


@dataclass
class InvestigationSummary:
    run_id: str
    question: str
    status: str
    started: datetime | None
    ended: datetime | None
    children: int
    has_findings: bool

    @property
    def elapsed_s(self) -> float | None:
        if self.started and self.ended:
            return (self.ended - self.started).total_seconds()
        return None


@dataclass
class Investigation:
    summary: InvestigationSummary
    root: Session
    findings_md: str = ""
    findings: dict | None = None                             # findings.json: the E10 contract, when written
    root_reply: str = ""                                     # root's streamed text, joined
    unattributed: list[OutputFile] = field(default_factory=list)  # outputs no child claims


class InvestigationSource(Protocol):
    def list_investigations(self) -> list[InvestigationSummary]: ...
    def get_investigation(self, run_id: str) -> Investigation | None: ...
