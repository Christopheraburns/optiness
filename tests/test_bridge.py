"""F1: the bridge's transport and auth boundary.

Needs fastmcp, which lives in the agent kernel's Python: `make test-bridge` runs
this file as a script inside the bridge (no pytest there). Under pytest on a host
without fastmcp, every test skips.
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path

try:
    import pytest
except ImportError:  # script mode in the bridge
    pytest = None

if pytest is not None:
    pytest.importorskip("fastmcp")

from starlette.testclient import TestClient  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.common.env import settings  # noqa: E402

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}}
ACCEPT = {"Accept": "application/json, text/event-stream"}


@contextmanager
def client(token: str):
    import tempfile
    old = {k: os.environ.get(k) for k in ("BRIDGE_BEARER_TOKEN", "BRIDGE_DATA_DIR")}
    os.environ["BRIDGE_BEARER_TOKEN"] = token
    os.environ["BRIDGE_DATA_DIR"] = tempfile.mkdtemp(prefix="bridge-test-")
    settings.cache_clear()
    try:
        from services.bridge.app import create_app
        with TestClient(create_app()) as c:
            yield c
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        settings.cache_clear()


def test_open_routes_need_no_token():
    with client("s3cret") as c:
        assert c.get("/healthz").text == "ok"
        info = c.get("/").json()
        assert info["mcp_endpoint"] == "/mcp" and info["store"]["schema"] >= 1


def test_mcp_rejects_missing_or_wrong_token():
    with client("s3cret") as c:
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "s3cret"}):
            r = c.post("/mcp", json=INIT, headers={**ACCEPT, **headers})
            assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Bearer"), headers


def test_mcp_initializes_with_token_and_any_host():
    with client("s3cret") as c:
        r = c.post("/mcp", json=INIT, headers={**ACCEPT, "Authorization": "Bearer s3cret",
                                               "Host": "bridge.example.trycloudflare.com"})
        assert r.status_code == 200, r.text
        assert r.json()["result"]["serverInfo"]["name"] == "prime-agent-mcp"


def test_no_configured_token_fails_closed():
    with client("") as c:
        assert c.post("/mcp", json=INIT, headers={**ACCEPT, "Authorization": "Bearer "}).status_code == 401


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
