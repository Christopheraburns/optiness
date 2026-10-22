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
