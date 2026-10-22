"""The console's client for the bridge (BURNDOWN H9): start, watch and stop investigations
through the same MCP tools Mistral Studio calls. Standard library only; the bearer token
stays server-side.

The console and the bridge are separate Workbench Applications, so the console reaches the
bridge at BRIDGE_INTERNAL_URL (local compose: the service name) or else BRIDGE_PUBLIC_URL.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from services.common.env import settings

PROTOCOL = "2025-06-18"
TIMEOUT_S = 30


class BridgeError(RuntimeError):
    """A failure worth showing to the operator as is (no secrets in the message)."""


def base_url() -> str:
    s = settings()
    return s.bridge_internal_url or s.bridge_public_url


def configured() -> list[str]:
    """What is missing for the console to start runs; empty when ready."""
    s = settings()
    problems = []
    if not base_url():
        problems.append("BRIDGE_PUBLIC_URL is not set")
    if not s.bridge_bearer_token:
        problems.append("BRIDGE_BEARER_TOKEN is not set")
    elif s.bridge_bearer_token == "change-me" and s.deploy_target != "local":
        # The .env.example placeholder is fine on a laptop, never on a reachable bridge.
        problems.append("BRIDGE_BEARER_TOKEN is still the placeholder 'change-me'")
    return problems


def _request(method: str, path: str, body: dict | None = None, auth: bool = True) -> tuple[int, str, str]:
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
               "MCP-Protocol-Version": PROTOCOL}
    if auth:
        headers["Authorization"] = f"Bearer {settings().bridge_bearer_token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base_url() + path, data, headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read().decode(errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise BridgeError(f"the bridge at {base_url()} is not reachable ({getattr(e, 'reason', e)})") from None


def _rpc(method: str, params: dict | None = None, rid: int = 1) -> dict:
    status, ctype, text = _request("POST", "/mcp", {"jsonrpc": "2.0", "id": rid, "method": method,
                                                    **({"params": params} if params else {})})
    if status == 401:
        raise BridgeError("the bridge refused the console's token: check BRIDGE_BEARER_TOKEN")
    if status != 200:
        raise BridgeError(f"the bridge answered HTTP {status} to {method}")
    if "text/event-stream" in ctype:  # tolerate an SSE-framed reply
        text = next((ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:")), "{}")
    msg = json.loads(text)
    if "error" in msg:
        raise BridgeError(f"{method}: {msg['error'].get('message', msg['error'])}")
    return msg["result"]


def call(tool: str, **arguments: Any) -> dict:
    """One tool call. The bridge is stateless (no MCP session), so each call stands alone."""
    _rpc("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                        "clientInfo": {"name": "investigation-console", "version": "1"}})
    result = _rpc("tools/call", {"name": tool, "arguments": arguments}, rid=2)
    if result.get("isError"):
        text = (result.get("content") or [{}])[0].get("text", "tool error")
        raise BridgeError(text)
    return result.get("structuredContent") or {}


def status() -> dict:
    """The bridge's open root route: runner state and models. Never raises."""
    if configured():
        return {"reachable": False, "problems": configured()}
    try:
        code, _, text = _request("GET", "/", auth=False)
        if code != 200:
            return {"reachable": False, "problems": [f"the bridge answered HTTP {code}"]}
        info = json.loads(text)
        return {"reachable": True, "problems": [], "runner": info.get("runner"),
                "root_model": info.get("root_model"), "child_model": info.get("child_model")}
    except BridgeError as e:
        return {"reachable": False, "problems": [str(e)]}
    except json.JSONDecodeError:
        return {"reachable": False, "problems": ["the bridge did not answer with JSON"]}
