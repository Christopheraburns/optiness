"""Console server (H1/H4): the read-only JSON API and the static front end, over the fixture runs."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")
from starlette.testclient import TestClient  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.console.runs import FileRunSource  # noqa: E402
from services.console.server import create_app, model_name  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "console"


@pytest.fixture()
def client():
    return TestClient(create_app(FileRunSource(FIX / "runs", FIX / "prime-agent", now=lambda: 4e9)))


def test_list_detail_and_static(client):
    runs = client.get("/api/investigations").json()
    assert runs and [r["run_id"] for r in runs] == sorted((r["run_id"] for r in runs), reverse=True)
    first = runs[0]
    for key in ("status", "question", "agents", "queries", "root_model", "child_models", "findings"):
        assert key in first
    detail = client.get(f"/api/investigations/{first['run_id']}").json()
    assert detail["root"]["role"] == "root" and isinstance(detail["root"]["children"], list)
    assert all("sql" in c and "result" in c for c in detail["root"]["cells"])
    assert client.get("/").headers["content-type"].startswith("text/html")
    assert client.get("/static/js/app.js").status_code == 200
    assert client.get("/healthz").text == "ok"


def test_bad_and_missing_run_ids(client):
    assert client.get("/api/investigations/..%2F..%2Fetc").status_code in (400, 404)
    assert client.get("/api/investigations/inv-19990101-000000").status_code == 404


def test_system_has_no_secrets(client, monkeypatch):
    from services.common import env
    env.settings.cache_clear()
    monkeypatch.setenv("IMPALA_PASSWORD", "hunter2-secret")
    monkeypatch.setenv("MISTRAL_API_KEY", "sk-secret")
    monkeypatch.setenv("BRIDGE_BEARER_TOKEN", "bearer-secret")
    try:
        body = client.get("/api/system").text
    finally:
        env.settings.cache_clear()
    assert "secret" not in body and "hunter2" not in body


def test_model_names():
    assert model_name("mistral/mistral-large-4") == "Mistral Large 4"
    assert model_name("mistral/mistral-medium-latest") == "Mistral Medium 3.5"
    assert model_name("mistral/some-new-model") == "Some New Model"
    assert model_name("") == ""


ACTION = {"X-Console-Action": "1"}


@pytest.fixture()
def bridge_calls(monkeypatch):
    """Stand in for the bridge: record tool calls, answer like it does."""
    from services.common import env
    from services.console import bridge
    env.settings.cache_clear()
    monkeypatch.setenv("BRIDGE_PUBLIC_URL", "https://bridge.example")
    monkeypatch.setenv("BRIDGE_BEARER_TOKEN", "t" * 32)
    calls = []

    def call(tool, **args):
        calls.append((tool, args))
        if tool == "start_investigation":
            return {"id": "inv-20261022-120000", "status": "queued", "queue_position": 1}
        if tool == "get_investigation":
            return {"id": args["id"], "status": "running", "question": "q",
                    "progress": {"subagents_spawned": 2, "subagents_reported": 1, "subagents_reprompted": 0,
                                 "subagents_failed": 0, "root_tool_calls": 7, "recent_events": []}}
        if tool == "cancel_investigation":
            return {"id": args["id"], "status": "cancelled"}
        raise AssertionError(tool)

    monkeypatch.setattr(bridge, "call", call)
    monkeypatch.setattr(bridge, "status", lambda: {"reachable": True, "problems": [], "runner": {"current": None, "queued": 0}})
    yield calls
    env.settings.cache_clear()


def test_launch_presets_preview_start_live_cancel(client, bridge_calls):
    info = client.get("/api/launch").json()
    assert [p["id"] for p in info["presets"]][:1] == ["decline-rate"] and info["bridge"]["reachable"]
    prompt = client.post("/api/launch/preview", headers=ACTION,
                         json={"question": "Why did declines rise?", "scope": "September", "subagents": 2}).json()["prompt"]
    assert "Question: Why did declines rise?" in prompt and "Scope from the requester: September" in prompt
    assert "Use 2 subagents" in prompt and "${" not in prompt
    started = client.post("/api/investigations", headers=ACTION, json={"question": "Why did declines rise?", "subagents": 2})
    assert started.status_code == 201 and started.json()["id"] == "inv-20261022-120000"
    assert bridge_calls[-1] == ("start_investigation", {"question": "Why did declines rise?", "subagents": 2})
    live = client.get("/api/live/inv-20261022-120000").json()
    assert live["available"] and live["progress"]["root_tool_calls"] == 7
    stopped = client.post("/api/investigations/inv-20261022-120000/cancel", headers=ACTION).json()
    assert stopped["status"] == "cancelled"


def test_actions_require_header_and_valid_input(client, bridge_calls):
    assert client.post("/api/investigations", json={"question": "Why did declines rise?", "subagents": 2}).status_code == 403
    assert client.post("/api/investigations", headers=ACTION, json={"question": "hm", "subagents": 2}).status_code == 400
    assert client.post("/api/investigations", headers=ACTION, json={"question": "Why did declines rise?", "subagents": 99}).status_code == 400
    assert client.post("/api/investigations", headers=ACTION, content=b"[1]").status_code == 400
    assert client.post("/api/investigations/../cancel", headers=ACTION).status_code in (400, 404)
    assert bridge_calls == []


def test_start_refused_with_placeholder_token_on_workbench(client, monkeypatch):
    from services.common import env
    env.settings.cache_clear()
    monkeypatch.setenv("DEPLOY_TARGET", "cai")
    monkeypatch.setenv("BRIDGE_PUBLIC_URL", "https://bridge.example")
    monkeypatch.setenv("BRIDGE_BEARER_TOKEN", "change-me")
    try:
        r = client.post("/api/investigations", headers=ACTION, json={"question": "Why did declines rise?", "subagents": 2})
    finally:
        env.settings.cache_clear()
    assert r.status_code == 502 and "placeholder" in r.json()["error"]


def test_detail_has_report_without_block_and_system_has_closing_slot(client, tmp_path, monkeypatch):
    from services.console import server
    runs = client.get("/api/investigations").json()
    with_report = next(r for r in runs if r["has_findings"])
    detail = client.get(f"/api/investigations/{with_report['run_id']}").json()
    assert detail["report_md"] and "```json" not in detail["report_md"].split("\n")[-1]
    assert client.get("/api/system").json()["closing_message"] is None  # empty in config/console.json: not shown
    cfg = tmp_path / "console.json"
    cfg.write_text('{"closing_message": "A line for the end."}')
    monkeypatch.setattr(server, "CONSOLE_CONFIG", cfg)
    assert client.get("/api/system").json()["closing_message"] == "A line for the end."
    cfg.write_text("not json")
    assert client.get("/api/system").json()["closing_message"] is None
