"""The bridge's investigation runner (BURNDOWN F3): one daemon-backed root per investigation.

Investigations queue in the store and run one at a time on a worker thread, through
the shared engine (services/common/investigation.py). One at a time is a constraint
of the engine, not a choice: it recognizes its root and children by start time.
Every engine event is written to the store's `events` table as it happens, the
status moves queued -> running -> done | failed | timeout | cancelled, and a valid
E10 contract lands in `findings`.

On start, `recover()` makes the store honest after a restart: a run that was
`running` when the bridge stopped is marked failed and the daemon sessions it left
are stopped (found through its `daemon_root` events, after making sure the daemon is
up: after a hard kill it restarts and resumes interrupted sessions); queued runs are
re-queued.
On stop, `shutdown()` cancels the running investigation and records it as interrupted.
"""
from __future__ import annotations

import logging
import queue
import threading
import traceback

from services.bridge.store import CANCELLED, DONE, FAILED, QUEUED, RUNNING, TIMEOUT, Store
from services.common import investigation as engine
from services.common.env import settings

log = logging.getLogger("bridge.runner")

OUTCOME_STATUS = {engine.FINDINGS_WRITTEN: DONE, engine.TIMEOUT: TIMEOUT, engine.CANCELLED: CANCELLED}


class Runner:
    def __init__(self, store: Store, investigate=engine.investigate, stop_tree=engine.stop_tree,
                 ensure_daemon=engine.ensure_daemon):
        self.store = store
        self.investigate = investigate
        self.stop_tree = stop_tree
        self.ensure_daemon = ensure_daemon
        self._shutting_down = False
        self._idle = threading.Event()
        self._idle.set()
        self._queue: queue.Queue[str] = queue.Queue()
        self._cancel: set[str] = set()
        self._current: str | None = None
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        """Start the worker (idempotent)."""
        with self._lock:
            if self._worker and self._worker.is_alive():
                return
            self._worker = threading.Thread(target=self._work, name="investigation-runner", daemon=True)
            self._worker.start()

    def recover(self) -> None:
        """Once, when the bridge starts: fail interrupted runs and stop the daemon sessions they
        left behind, then re-queue waiting ones."""
        interrupted = self.store.list_investigations(limit=1000, status=RUNNING)
        # Always: a container restart leaves the daemon down with a stale socket, and the daemon
        # that the next run would start revives any session that was mid-turn, unsupervised.
        if not self.ensure_daemon():
            # Its sessions cannot be listed now; the daemon will restore them when it starts.
            log.warning("prime-agent daemon did not answer; interrupted runs' sessions not stopped")
        for inv in interrupted:
            roots = [e["data"]["id"] for e in self.store.events(inv["id"], limit=100_000)
                     if e["kind"] == "daemon_root"]
            stopped = self.stop_tree(roots) if roots else []
            if stopped:
                self.store.add_event(inv["id"], "sessions_stopped", {"ids": stopped, "by": "recover"})
            self.store.set_status(inv["id"], FAILED, outcome="interrupted",
                                  error="the bridge stopped while this investigation was running")
            log.info("recovered %s: marked failed, stopped %d orphaned sessions", inv["id"], len(stopped))
        for inv in reversed(self.store.list_investigations(limit=1000, status=QUEUED)):
            self._queue.put(inv["id"])

    # ------------------------------------------------------------- requests
    def submit(self, question: str, *, scope: dict | None = None, children: int | None = None,
               parent_id: str | None = None) -> dict:
        """Queue an investigation; returns its store row. Starts the worker if needed."""
        s = settings()
        children = children or min(3, s.max_children)
        if not 1 <= children <= s.max_children:
            raise ValueError(f"children must be 1..{s.max_children}")
        if not question or not question.strip():
            raise ValueError("question is empty")
        inv = self.store.create_investigation(question.strip(), scope=scope, parent_id=parent_id,
                                              root_model=s.root_model.selector,
                                              child_model=s.child_model.selector, children=children)
        self._queue.put(inv["id"])
        self.start()
        return inv

    def cancel(self, inv_id: str) -> dict:
        """Cancel a queued investigation now, or ask a running one to stop at its next poll."""
        inv = self.store.get_investigation(inv_id)
        if inv is None:
            raise KeyError(inv_id)
        if inv["status"] == QUEUED:
            return self.store.set_status(inv_id, CANCELLED, outcome="cancelled before it started")
        if inv["status"] == RUNNING:
            self._cancel.add(inv_id)
            self.store.add_event(inv_id, "cancel_requested")
        return self.store.get_investigation(inv_id)

    def task_question(self, inv: dict) -> str:
        """The question as the agent receives it: the requester's words, their scope, and for a
        follow-up the parent investigation's findings (stated as prior results to verify, not facts)."""
        parts = [inv["question"]]
        if (inv.get("scope") or {}).get("text"):
            parts.append(f"Scope from the requester: {inv['scope']['text']}")
        if inv.get("parent_id"):
            prior = self.store.get_findings(inv["parent_id"])
            if prior["findings"]:
                claims = "\n".join(f"- {f['claim']} (confidence {f['confidence']})" for f in prior["findings"])
                parts.append(f"This follows up investigation {inv['parent_id']}, which reported:\n{claims}\n"
                             "Treat these as prior results: re-measure anything your answer depends on.")
        return "\n\n".join(parts)

    def queue_position(self, inv_id: str) -> int | None:
        """1-based position among queued investigations, or None if not queued."""
        queued = [i["id"] for i in reversed(self.store.list_investigations(limit=1000, status=QUEUED))]
        return queued.index(inv_id) + 1 if inv_id in queued else None

    def shutdown(self, timeout: float = 30) -> bool:
        """Bridge stopping: cancel the running investigation (its sessions are stopped and it is
        recorded as interrupted) and wait up to `timeout` seconds. True if nothing is left running."""
        self._shutting_down = True
        if self._current:
            self._cancel.add(self._current)
        return self._idle.wait(timeout)

    @property
    def current(self) -> str | None:
        return self._current

    def queued(self) -> int:
        return self._queue.qsize()

    # ------------------------------------------------------------- worker
    def _work(self) -> None:
        while True:
            inv_id = self._queue.get()
            try:
                self._run(inv_id)
            except Exception:  # noqa: BLE001  never let one run kill the worker
                log.exception("investigation %s crashed the runner", inv_id)
                try:
                    self.store.set_status(inv_id, FAILED, outcome="runner error",
                                          error=traceback.format_exc(limit=3)[-2000:])
                except Exception:  # noqa: BLE001
                    log.exception("could not record the failure of %s", inv_id)
            finally:
                self._current = None
                self._cancel.discard(inv_id)
                self._idle.set()

    def _run(self, inv_id: str) -> None:
        inv = self.store.get_investigation(inv_id)
        if inv is None or inv["status"] != QUEUED or self._shutting_down:
            return  # cancelled while queued, already handled, or the bridge is stopping (stays queued)
        self._current = inv_id
        self._idle.clear()
        self.store.set_status(inv_id, RUNNING)
        log.info("investigation %s started: %s", inv_id, inv["question"][:120])

        def emit(kind: str, data: dict, session: str | None = None) -> None:
            if kind == "run_started":
                self.store.update_investigation(inv_id, run_dir=data["run_dir"])
            elif kind == "acp_session":
                self.store.update_investigation(inv_id, acp_session_id=data["session_id"])
            self.store.add_event(inv_id, kind, data, session)

        result = self.investigate(self.task_question(inv), children=inv["children"], child_model=inv["child_model"],
                                  run_id=inv_id, emit=emit, cancelled=lambda: inv_id in self._cancel)
        if result.contract:
            self.store.set_findings(inv_id, result.contract)
        status = OUTCOME_STATUS.get(result.outcome, FAILED)
        outcome, error = result.outcome, result.error
        if status == CANCELLED and self._shutting_down:
            status, outcome, error = FAILED, "interrupted", "the bridge stopped while this investigation was running"
        self.store.set_status(inv_id, status, outcome=outcome, error=error if status != DONE else None)
        log.info("investigation %s %s: %s in %.0fs", inv_id, status, result.outcome, result.elapsed_s)
