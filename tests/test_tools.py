"""F4: the MCP tools, end to end over an in-memory MCP client with a fake engine.

Needs fastmcp, which lives in the agent kernel's Python: `make test-bridge` runs this
file as a script inside the bridge (no pytest there). Under pytest on a host without
fastmcp, every test skips.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import threading
import time
from pathlib import Path

try:
    import pytest
except ImportError:  # script mode in the bridge
    pytest = None

if pytest is not None:
    pytest.importorskip("fastmcp")

from fastmcp import Client, FastMCP  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.bridge import tools  # noqa: E402
from services.bridge.runner import Runner  # noqa: E402
from services.bridge.store import Store  # noqa: E402
from services.common import investigation as engine  # noqa: E402

EV = [{"session": "slice-1", "sql": "SELECT COUNT(*) FROM payment_transactions", "rows": 1, "elapsed_ms": 12}]
CONTRACT = {"findings": [{"claim": "Declines rose.", "confidence": "high", "evidence": EV}],
            "ruled_out": [{"explanation": "Weekends", "reason": "Flat.", "evidence": EV}]}
REPORT = "# Findings\n\nDeclines rose.\n\n```json\n{}\n```\n"


class Fake:
    """Stands in for engine.investigate; `hold` keeps a run going until released."""

    def __init__(self, root: Path):
        self.root, self.questions, self.hold = root, [], threading.Event()
        self.hold.set()

    def __call__(self, question, *, children, child_model, run_id, emit, cancelled):
        self.questions.append(question)
        run_dir = self.root / run_id
        run_dir.mkdir(parents=True)
        emit("run_started", {"run_id": run_id, "run_dir": str(run_dir)}, None)
        emit("child_spawned", {"id": "c1"}, "slice-1")
        emit("root_message", {"text": "Spawned 1 children; waiting."}, "root")
        while not self.hold.wait(0.01):
            if cancelled():
                return engine.RunResult(run_id, run_dir, engine.CANCELLED)
        emit("child_reported", {"id": "c1"}, "slice-1")
        (run_dir / "findings.md").write_text(REPORT)
        return engine.RunResult(run_id, run_dir, engine.FINDINGS_WRITTEN, contract=CONTRACT)


def setup():
    tmp = Path(tempfile.mkdtemp(prefix="tools-test-"))
    store = Store(tmp / "db")
    fake = Fake(tmp / "runs")
    runner = Runner(store, fake)
    runner.start()
    mcp = FastMCP("t")
    tools.register(mcp, lambda: runner)
    return mcp, store, fake


async def call(c, name, **args):
    r = await c.call_tool(name, args, raise_on_error=False)
    return r.is_error, (r.structured_content if not r.is_error else r.content[0].text)


async def wait_done(c, inv_id, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        _, v = await call(c, "get_investigation", id=inv_id)
        if v["status"] not in ("queued", "running"):
            return v
        await asyncio.sleep(0.02)
    raise AssertionError(f"{inv_id} did not finish")


def run(coro):
    return asyncio.run(coro)


def test_six_tools_with_descriptions_and_schemas():
    mcp, _, _ = setup()

    async def go():
        async with Client(mcp) as c:
            listed = {t.name: t for t in await c.list_tools()}
        assert set(listed) == {"start_investigation", "get_investigation", "get_findings", "ask_followup",
                               "list_investigations", "cancel_investigation"}
        for t in listed.values():
            assert len(t.description) > 60 and t.inputSchema["type"] == "object" and t.outputSchema
        assert listed["get_findings"].annotations.readOnlyHint
    run(go())


def test_start_poll_and_fetch_findings():
    mcp, _, fake = setup()
    fake.hold.clear()

    async def go():
        async with Client(mcp) as c:
            err, started = await call(c, "start_investigation", question="Why did declines rise?", subagents=2)
            assert not err and started["id"].startswith("inv-") and "get_investigation" in started["next_step"]
            err, early = await call(c, "get_findings", id=started["id"])
            assert not err and early["ready"] is False and early["findings"] == []
            fake.hold.set()
            done = await wait_done(c, started["id"])
            assert done["status"] == "done" and "get_findings" in done["next_step"]
            p = done["progress"]
            assert (p["subagents_spawned"], p["subagents_reported"]) == (1, 1)
            assert p["latest_root_message"].startswith("Spawned")
            err, f = await call(c, "get_findings", id=started["id"])
            assert not err and f["ready"] and f["findings"] == CONTRACT["findings"]
            assert f["ruled_out"][0]["explanation"] == "Weekends"
            assert f["report_markdown"] == "# Findings\n\nDeclines rose."  # contract block stripped
            err, lst = await call(c, "list_investigations")
            assert [i["id"] for i in lst["investigations"]] == [started["id"]]
    run(go())


def test_followup_gets_prior_findings_and_scope_reaches_the_agent():
    mcp, store, fake = setup()

    async def go():
        async with Client(mcp) as c:
            _, first = await call(c, "start_investigation", question="Why did declines rise?",
                                  scope="September only")
            await wait_done(c, first["id"])
            err, follow = await call(c, "ask_followup", id=first["id"], question="Which merchants were hit?")
            assert not err
            await wait_done(c, follow["id"])
        assert "Scope from the requester: September only" in fake.questions[0]
        assert "Declines rose. (confidence high)" in fake.questions[1] and first["id"] in fake.questions[1]
        assert store.get_investigation(follow["id"])["parent_id"] == first["id"]
    run(go())


def test_errors_are_tool_errors():
    mcp, _, fake = setup()
    fake.hold.clear()

    async def go():
        async with Client(mcp) as c:
            assert (await call(c, "get_investigation", id="inv-20990101-000000"))[0]
            assert (await call(c, "get_findings", id="not-an-id"))[0]
            _, s = await call(c, "start_investigation", question="Why did declines rise?")
            err, msg = await call(c, "ask_followup", id=s["id"], question="And then what?")
            assert err and "wait for it to finish" in msg
            assert (await call(c, "start_investigation", question="Why?", subagents=99))[0]
            _, queued = await call(c, "start_investigation", question="A second question")
            err, v = await call(c, "cancel_investigation", id=queued["id"])
            assert not err and v["status"] == "cancelled"
            err, v = await call(c, "cancel_investigation", id=s["id"])
            assert not err
            done = await wait_done(c, s["id"])
            assert done["status"] == "cancelled"
            assert (await call(c, "cancel_investigation", id=s["id"]))[0]  # already ended
    run(go())


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
