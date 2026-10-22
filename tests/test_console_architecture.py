"""Architecture (H11): live status per part, without a real bridge or warehouse, and no host in the output."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")
from starlette.testclient import TestClient  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.common import env  # noqa: E402
from services.console import architecture, bridge  # noqa: E402
from services.console.runs import FileRunSource  # noqa: E402
from services.console.server import create_app  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "console"
HOST = "coordinator-engine-name.example.cloud"


@pytest.fixture()
def client(monkeypatch):
    env.settings.cache_clear()
    architecture._probe.clear()
    monkeypatch.setenv("IMPALA_HOST", HOST)
    monkeypatch.setenv("BRIDGE_PUBLIC_URL", "https://bridge.example")
    monkeypatch.setenv("BRIDGE_BEARER_TOKEN", "t" * 32)
    yield TestClient(create_app(FileRunSource(FIX / "runs", FIX / "prime-agent", now=lambda: 4e9)))
    env.settings.cache_clear()
    architecture._probe.clear()


def test_everything_up(client, monkeypatch):
    monkeypatch.setattr(architecture, "run_query", lambda sql, max_rows=None: None)
    monkeypatch.setattr(bridge, "status", lambda: {"reachable": True, "problems": [], "runner": {"current": None, "queued": 2},
                                                   "root_model": "mistral/mistral-large-4", "child_model": "mistral/mistral-medium-latest"})
    r = client.get("/api/architecture")
    body = r.json()
    assert body["studio"] == {"state": "configured", "endpoint": "https://bridge.example/mcp",
                              "detail": "Mistral Studio reaches the harness through this MCP endpoint with the project's bearer token."}
    assert body["harness"]["state"] == "up" and body["harness"]["queued"] == 2
    assert body["warehouse"]["state"] == "up" and body["warehouse"]["latency_ms"] >= 0
    assert body["models"]["root"] == "Mistral Large 4" and body["models"]["source"] == "bridge"  # not the console's defaults
    assert body["runs"]["total"] >= 1
    assert body["latest"] and body["latest"]["has_findings"]
    assert HOST not in r.text and "engine-name" not in r.text  # the host never leaves the server


def test_down_and_unconfigured(client, monkeypatch):
    def boom(sql, max_rows=None):
        raise RuntimeError("connection refused")
    monkeypatch.setattr(architecture, "run_query", boom)
    monkeypatch.setattr(bridge, "status", lambda: {"reachable": False, "problems": ["the bridge at https://bridge.example is not reachable (timed out)"]})
    body = client.get("/api/architecture").json()
    assert body["warehouse"]["state"] == "down" and "connection refused" in body["warehouse"]["detail"]
    assert body["harness"]["state"] == "down"
    monkeypatch.setenv("BRIDGE_BEARER_TOKEN", "")
    monkeypatch.setenv("IMPALA_HOST", "")
    env.settings.cache_clear()
    monkeypatch.setattr(bridge, "status", lambda: {"reachable": False, "problems": bridge.configured()})
    body = client.get("/api/architecture").json()
    assert body["studio"]["state"] == "not_configured" and body["harness"]["state"] == "not_configured"
    assert body["warehouse"]["state"] == "not_configured"
