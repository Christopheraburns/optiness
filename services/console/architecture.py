"""Architecture (BURNDOWN H11): each part of the system and whether it is up right now.

Status comes from what the console can observe: the bridge's open status route (runner, models),
a timed read-only probe of the warehouse, the project's bridge settings, and the run directories.
What it cannot observe (whether Mistral Studio has registered the connector) it reports as
configuration, never as a fact. The warehouse host is not returned: its name can identify the
engine, and the console stays engine-neutral.
"""
from __future__ import annotations

import threading
import time
from typing import Any

from services.common.env import settings
from services.common.impala import run_query
from services.console import bridge

PROBE_CACHE_S = 30
_lock = threading.Lock()
_probe: dict[str, Any] = {}


def warehouse() -> dict[str, Any]:
    s = settings().impala
    if not s.host:
        return {"state": "not_configured", "detail": "No warehouse login is configured for the console."}
    with _lock:
        if _probe and time.time() - _probe["at"] < PROBE_CACHE_S:
            return _probe["data"]
        started = time.perf_counter()
        try:
            run_query("SELECT 1", max_rows=1)
            data = {"state": "up", "database": s.database, "latency_ms": int((time.perf_counter() - started) * 1000),
                    "tls": s.use_ssl, "transport": "http" if s.use_http else "binary"}
        except Exception as e:  # noqa: BLE001 - report the warehouse's own message, never secrets
            data = {"state": "down", "database": s.database, "detail": f"{type(e).__name__}: {str(e)[:200]}"}
        _probe.update(at=time.time(), data=data)
        return data


def harness() -> dict[str, Any]:
    info = bridge.status()
    if not info.get("reachable"):
        problems = info.get("problems") or ["unreachable"]
        state = "not_configured" if any("not set" in p for p in problems) else "down"
        return {"state": state, "detail": "; ".join(problems)}
    runner = info.get("runner") or {}
    return {"state": "up", "current": runner.get("current"), "queued": runner.get("queued", 0),
            "root_model": info.get("root_model"), "child_model": info.get("child_model")}


def studio() -> dict[str, Any]:
    s = settings()
    problems = bridge.configured()
    return {"state": "configured" if not problems else "not_configured",
            "endpoint": f"{s.bridge_public_url}/mcp" if s.bridge_public_url else None,
            "detail": "; ".join(problems) if problems else
            "Mistral Studio reaches the harness through this MCP endpoint with the project's bearer token."}
