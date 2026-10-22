"""File-backed InvestigationSource: reads `$AGENT_WORKDIR/runs/<run_id>/` as
written by scripts/run_investigation.py, plus the child transcripts prime-agent
keeps under `$PRIME_AGENT_CODING_AGENT_DIR/session-artifacts/`.

What each file gives us:
  task.md        the question (line starting "Question:")
  session.json   ACP session id
  events.jsonl   the root's ACP stream: code cells and results, streamed text,
                 child spawn/status (`session_info_update` _meta.subagents),
                 end of turn (`result.stopReason`). No timestamps.
  findings.md    the root's report, shown as-is
  findings.json  the findings contract (claims, evidence, ruled out), when the run wrote it
  *.json, *.csv  child outputs; free-form, possibly invalid JSON
  session-artifacts/<root sid>/sub-<id>/rlm-subagent.json + <sid>.jsonl
                 per child: name, model, status, prompt (names its output
                 file), and a timestamped transcript (cells, tokens)
  sessions/<root sid>.jsonl
                 the root's own transcript (timestamps, tokens)

Everything is optional: a run with no agent dir still lists, just with less
detail. Parsed runs are cached on the files' (mtime, size).
"""
from __future__ import annotations

import ast
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from services.console.model import (
    ABANDONED, DONE, ENDED, RUNNING, STALLED,
    Cell, Evidence, Investigation, InvestigationSummary, Message, OutputFile, Session,
)

META = "ai.primeintellect.prime-agent"
RUN_ID = re.compile(r"^inv-(\d{8})-(\d{6})$")
OUTPUT_SUFFIXES = {".json", ".csv"}
RESERVED = {"session.json", "findings.json"}
CHILD_TAG = re.compile(r"^\[(?:agent-message from|child-exited: [\w-]+) child:([^\]]+)\]\s*")
MAX_OUTPUT_BYTES = 512_000
SQL_START = re.compile(r"^\s*(SELECT|WITH|SHOW|DESCRIBE|EXPLAIN|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER)\b", re.I)
# Errors that show up in a cell's output even when the model caught the exception.
ERROR_TEXT = re.compile(r"Traceback \(most recent call last\)|ParseException|AnalysisException|HiveServer2Error|Error executing|^Tool \S+ not found")


# --- small parsers ----------------------------------------------------------

def read_jsonl(path: Path) -> Iterable[dict]:
    """Yield JSON objects, skipping blank and partial lines (a live run may be
    mid-write)."""
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except FileNotFoundError:
        return


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def run_started(run_id: str) -> datetime | None:
    """run_investigation.py names runs inv-YYYYMMDD-HHMMSS in UTC."""
    m = RUN_ID.match(run_id)
    if not m:
        return None
    return datetime.strptime("".join(m.groups()), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)


def question_of(task_md: str) -> str:
    for line in task_md.splitlines():
        if line.startswith("Question:"):
            return line.removeprefix("Question:").strip()
    return ""


def sql_in_code(code: str) -> list[str]:
    """SQL statements written as string literals in a Python cell. f-string
    fields are kept as {expr}."""
    found: list[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        for m in re.finditer(r'"""(.*?)"""|\'\'\'(.*?)\'\'\'', code, re.S):
            s = m.group(1) or m.group(2) or ""
            if SQL_START.match(s):
                found.append(s.strip())
        return found
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            s = "".join(
                v.value if isinstance(v, ast.Constant) else "{" + ast.unparse(v.value) + "}"
                for v in node.values
            )
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value
        else:
            continue
        if SQL_START.match(s):
            found.append(s.strip())
    # Constants inside an f-string are walked too; drop fragments of a statement already kept.
    return [s for s in dict.fromkeys(found) if not any(s != o and s in o for o in found)]


def evidence_in(data: Any, path: str = "") -> list[Evidence]:
    """Every dict with a string `sql` key, wherever the child put it."""
    out: list[Evidence] = []
    if isinstance(data, dict):
        sql = data.get("sql")
        if not isinstance(sql, str) and isinstance(data.get("query"), str) and SQL_START.match(data["query"]):
            sql = data["query"]
        if isinstance(sql, str):
            out.append(Evidence(
                sql=sql.strip(),
                row_count=_int(data.get("row_count", data.get("rows"))),
                elapsed_ms=_int(data.get("elapsed_ms")),
                truncated=data.get("truncated") if isinstance(data.get("truncated"), bool) else None,
                path=path or "(top level)",
            ))
        for k, v in data.items():
            if v is not sql:
                out += evidence_in(v, f"{path}.{k}" if path else str(k))
    elif isinstance(data, list):
        for i, v in enumerate(data):
            out += evidence_in(v, f"{path}[{i}]")
    return out


def _int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def read_output(path: Path) -> OutputFile:
    raw = path.read_bytes()[:MAX_OUTPUT_BYTES]
    text = raw.decode("utf-8", errors="replace")
    out = OutputFile(name=path.name, text=text)
    if path.suffix != ".json":
        return out
    try:
        out.data = json.loads(text)
        out.evidence = evidence_in(out.data)
    except json.JSONDecodeError as e:
        out.parse_error = str(e)
        # Salvage the cited SQL from a file the child got wrong.
        for m in re.finditer(r'"sql"\s*:\s*"((?:\\.|[^"\\])*)"', text):
            try:
                out.evidence.append(Evidence(sql=json.loads(f'"{m.group(1)}"').strip(), path="(unparsed)"))
            except json.JSONDecodeError:
                pass
    return out


def result_text(content: Any) -> str:
    """Text of an ACP tool_call_update content list or a transcript toolResult."""
    parts: list[str] = []
    for c in content or []:
        if not isinstance(c, dict):
            continue
        inner = c.get("content") if isinstance(c.get("content"), dict) else c
        if isinstance(inner.get("text"), str):
            parts.append(inner["text"])
    return "\n".join(parts)


# --- events.jsonl (root, ACP) ----------------------------------------------

class RootEvents:
    def __init__(self) -> None:
        self.cells: dict[str, Cell] = {}
        self.subagents: dict[str, dict] = {}     # child id -> latest {sessionName, status, model}
        self.reply: dict[str, list[str]] = {}    # messageId -> text chunks
        self.ended = False
        self.model = ""

    @classmethod
    def parse(cls, path: Path) -> RootEvents:
        ev = cls()
        for msg in read_jsonl(path):
            result = msg.get("result")
            if isinstance(result, dict):
                if "stopReason" in result:
                    ev.ended = True
                for opt in result.get("configOptions") or []:
                    if opt.get("id") == "model":
                        try:
                            ev.model = "/".join(json.loads(opt.get("currentValue", "")))
                        except (json.JSONDecodeError, TypeError):
                            ev.model = str(opt.get("currentValue", ""))
                continue
            if msg.get("method") != "session/update":
                continue
            ev._update((msg.get("params") or {}).get("update") or {})
        return ev

    def _update(self, u: dict) -> None:
        kind = u.get("sessionUpdate")
        meta = (u.get("_meta") or {}).get(META) or {}
        if kind == "tool_call":
            code = (u.get("rawInput") or {}).get("code", "")
            self.cells[u.get("toolCallId", str(len(self.cells)))] = Cell(code=code, sql=sql_in_code(code))
        elif kind == "tool_call_update":
            cell = self.cells.setdefault(u.get("toolCallId", ""), Cell(code=""))
            cell.result = result_text(u.get("content"))
            cell.ok = u.get("status") != "failed" and not ERROR_TEXT.search(cell.result)
        elif kind == "agent_message_chunk":
            text = (u.get("content") or {}).get("text")
            if isinstance(text, str):
                self.reply.setdefault(u.get("messageId", ""), []).append(text)
        elif kind == "session_info_update":
            for sub in meta.get("subagents") or []:
                if sub.get("id"):
                    self.subagents.setdefault(sub["id"], {}).update(sub)
            if meta.get("phase") == "terminalQuiescence":
                self.ended = True

    @property
    def reply_text(self) -> str:
        return "\n\n".join("".join(chunks).strip() for chunks in self.reply.values() if "".join(chunks).strip())


# --- prime-agent transcripts -----------------------------------------------

def transcript_session(path: Path, sess: Session) -> None:
    """Fill timing, tokens, cells (with their times) and messages from a prime-agent session transcript."""
    first = last = None
    tokens = 0
    pending: dict[str, Cell] = {}
    cells: list[Cell] = []
    messages: list[Message] = []
    for e in read_jsonl(path):
        ts = parse_ts(e.get("timestamp"))
        if ts:
            first = first or ts
            last = ts
        if e.get("type") == "custom_message" and isinstance(e.get("content"), str):
            kind = {"agent_message": "report", "rlm_child_terminal_notice": "notice"}.get(e.get("customType"))
            if kind:
                tag = CHILD_TAG.match(e["content"])
                messages.append(Message(at=ts, kind=kind, text=e["content"][tag.end():] if tag else e["content"],
                                        sender=tag.group(1) if tag else ""))
            continue
        if e.get("type") != "message":
            continue
        m = e.get("message") or {}
        if m.get("role") == "assistant":
            tokens += ((m.get("usage") or {}).get("totalTokens") or 0)
            text = "".join(p.get("text", "") for p in m.get("content") or [] if isinstance(p, dict) and p.get("type") == "text").strip()
            if text:
                messages.append(Message(at=ts, kind="text", text=text))
            for part in m.get("content") or []:
                if isinstance(part, dict) and part.get("type") == "toolCall":
                    args = part.get("arguments") or {}
                    code = args.get("code", "")
                    if not code:  # a tool other than the REPL, often one that does not exist
                        code = f"# tool call: {part.get('name', '?')}\n{json.dumps(args, indent=2)}"
                    cell = Cell(code=code, sql=sql_in_code(code), started=ts)
                    pending[part.get("id", "")] = cell
                    cells.append(cell)
        elif m.get("role") == "toolResult":
            cell = pending.pop(m.get("toolCallId", ""), None)
            if cell is None:
                continue
            details = m.get("details") or {}
            cell.result = result_text(m.get("content"))
            cell.ended = ts
            cell.duration_ms = _int(details.get("durationMs"))
            cell.ok = details.get("status") != "error" and not m.get("isError") and not ERROR_TEXT.search(cell.result)
    sess.started, sess.ended = first, last
    sess.tokens = tokens or None
    sess.messages = messages
    if cells:
        sess.cells = cells


# --- the source --------------------------------------------------------------

class FileRunSource:
    def __init__(
        self,
        runs_dir: Path,
        agent_dir: Path | None = None,
        stale_after_s: float = 300,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.runs_dir = Path(runs_dir)
        self.agent_dir = Path(agent_dir) if agent_dir else None
        self.stale_after_s = stale_after_s
        self.now = now
        self._cache: dict[str, tuple[tuple, Investigation]] = {}
        self._root_transcripts: dict[str, Path] = {}

    def list_investigations(self) -> list[InvestigationSummary]:
        out = []
        for run_dir in self._run_dirs():
            inv = self.get_investigation(run_dir.name)
            if inv:
                out.append(inv.summary)
        return out

    def get_investigation(self, run_id: str) -> Investigation | None:
        run_dir = self.runs_dir / run_id
        if not RUN_ID.match(run_id) or not run_dir.is_dir():
            return None
        artifacts = self._artifact_index()
        sig = self._signature(run_dir, artifacts)
        cached = self._cache.get(run_id)
        if cached and cached[0] == sig:
            return cached[1]
        inv = self._build(run_dir, artifacts)
        self._cache[run_id] = (sig, inv)
        return inv

    # ---------------------------------------------------------------------

    def _transcript_naming(self, run_id: str) -> Path | None:
        """The root transcript for a run with no children yet: its task text contains the run id.
        Only transcripts written since the run started are read, and only their first 64 KB."""
        if not self.agent_dir or not (self.agent_dir / "sessions").is_dir():
            return None
        if run_id in self._root_transcripts:
            return self._root_transcripts[run_id]
        started = run_started(run_id)
        since = started.timestamp() - 120 if started else 0
        needle = run_id.encode()
        for path in sorted((self.agent_dir / "sessions").glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
            if path.stat().st_mtime < since:
                break
            with path.open("rb") as f:
                if needle in f.read(65_536):
                    self._root_transcripts[run_id] = path
                    return path
        return None

    def _run_dirs(self) -> list[Path]:
        if not self.runs_dir.is_dir():
            return []
        dirs = [p for p in self.runs_dir.iterdir() if p.is_dir() and RUN_ID.match(p.name)]
        return sorted(dirs, key=lambda p: p.name, reverse=True)

    def _artifact_index(self) -> dict[str, Path]:
        """child id -> its session-artifacts directory."""
        if not self.agent_dir:
            return {}
        root = self.agent_dir / "session-artifacts"
        if not root.is_dir():
            return {}
        return {p.parent.name: p.parent for p in root.glob("*/sub-*/rlm-subagent.json")}

    def _signature(self, run_dir: Path, artifacts: dict[str, Path]) -> tuple:
        # Child artifacts are folded in only once the run names them, so the
        # signature is the run's files plus every artifact file (cheap: stat only).
        files = [p for p in run_dir.iterdir() if p.is_file()]
        files += [f for d in artifacts.values() for f in d.iterdir() if f.suffix in (".json", ".jsonl")]
        stale = self._fresh(run_dir)
        return (stale, *sorted((str(f), f.stat().st_mtime_ns, f.stat().st_size) for f in files if f.exists()))

    def _last_activity(self, run_dir: Path) -> float:
        times = [p.stat().st_mtime for p in run_dir.iterdir() if p.is_file()]
        return max(times) if times else 0.0

    def _fresh(self, run_dir: Path) -> bool:
        return self.now() - self._last_activity(run_dir) < self.stale_after_s

    def _build(self, run_dir: Path, artifacts: dict[str, Path]) -> Investigation:
        run_id = run_dir.name
        task = _read_text(run_dir / "task.md")
        findings = _read_text(run_dir / "findings.md")
        session = _read_json(run_dir / "session.json") or {}
        ev = RootEvents.parse(run_dir / "events.jsonl")

        outputs = {
            p.name: read_output(p)
            for p in sorted(run_dir.iterdir())
            if p.is_file() and p.suffix in OUTPUT_SUFFIXES and p.name not in RESERVED
        }

        fresh = self._fresh(run_dir)
        if findings and (ev.ended or not fresh):
            status = DONE
        elif ev.ended:
            status = ENDED
        elif fresh:
            status = RUNNING
        else:
            status = STALLED

        root = Session(
            id=session.get("session_id", ""), name="root", role="root",
            model=ev.model, status=status, cells=list(ev.cells.values()),
        )

        root_sid = ""
        claimed: set[str] = set()
        for child_id, info in ev.subagents.items():
            child = Session(
                id=child_id, name=info.get("sessionName", child_id), role="child",
                model=info.get("model", ""), status=info.get("status", ""),
            )
            art = artifacts.get(child_id)
            if art:
                root_sid = root_sid or art.parent.name
                meta = _read_json(art / "rlm-subagent.json") or {}
                child.status = meta.get("status") or child.status
                model = meta.get("model") or {}
                if isinstance(model, dict) and model.get("modelId"):
                    child.model = f"{model.get('provider', '')}/{model['modelId']}".lstrip("/")
                for name, out in outputs.items():
                    if name not in claimed and re.search(rf"(?<![\w-]){re.escape(name)}\b", meta.get("prompt", "")):
                        child.output = out
                        claimed.add(name)
                        break
                transcript = next((p for p in art.glob("*.jsonl") if p.name != "semantic-edges.jsonl"), None)
                if transcript:
                    transcript_session(transcript, child)
            if status != RUNNING and child.status == "running":
                child.status = ABANDONED
            root.children.append(child)

        root_transcript = self.agent_dir / "sessions" / f"{root_sid}.jsonl" if (self.agent_dir and root_sid) else None
        if not (root_transcript and root_transcript.exists()):
            # No child yet to point at the root: find the transcript whose task names this run.
            root_transcript = self._transcript_naming(run_id)
        if root_transcript and root_transcript.exists():
            cells = root.cells
            transcript_session(root_transcript, root)
            # Events are the contract for the root's cells; the transcript supplies their times,
            # matched in order by the code they ran.
            timed: dict[str, list[Cell]] = {}
            for c in root.cells:
                timed.setdefault(c.code.strip(), []).append(c)
            for c in cells:
                match = timed.get(c.code.strip())
                if match:
                    t = match.pop(0)
                    c.started, c.ended = t.started, t.ended
                    c.duration_ms = c.duration_ms or t.duration_ms
            root.cells = cells
        if not root.started:
            root.started = run_started(run_id)
        if status != RUNNING:
            last = datetime.fromtimestamp(self._last_activity(run_dir), tz=timezone.utc)
            root.ended = root.ended or last
        else:
            root.ended = None

        summary = InvestigationSummary(
            run_id=run_id, question=question_of(task), status=status,
            started=root.started, ended=root.ended,
            children=len(root.children), has_findings=bool(findings),
        )
        contract = _read_json(run_dir / "findings.json")
        return Investigation(
            summary=summary, root=root, findings_md=findings, root_reply=ev.reply_text,
            findings=contract if isinstance(contract, dict) else None,
            unattributed=[o for n, o in outputs.items() if n not in claimed],
        )


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
