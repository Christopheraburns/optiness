"""prime-agent-mcp bridge: the MCP server Mistral Studio's connector calls.

FastMCP over Streamable HTTP in stateless JSON mode (no session affinity, so a
Workbench application restart or a second replica does not strand a client).
Routes: `/mcp` (MCP, bearer token required), `/healthz` and `/` (open). This
module owns the transport and the auth boundary (F1); the job store (F2), the
runner (F3) and the tools (F4) live beside it. Honors the application contract: CDSW_APP_PORT, BIND_HOST.
"""
from __future__ import annotations

# --- interpreter shim -------------------------------------------------------
# Workbench launches application scripts with the runtime's default python,
# and the local stack mirrors that. The packages we need live in the
# pre-warmed kernel venv, so re-exec under it once if we are not already there.
import os as _os, sys as _sys  # noqa: E402
_KP = _os.environ.get("PRIME_AGENT_KERNEL_PYTHON")
# The venv's python is a symlink to the base interpreter, so compare the
# environment prefix (venv dir), not the executable path.
if _KP and _os.path.exists(_KP):
    _venv = _os.path.realpath(_os.path.dirname(_os.path.dirname(_KP)))
    if _os.path.realpath(_sys.prefix) != _venv:
        _os.execv(_KP, [_KP, *_sys.argv])
# ---------------------------------------------------------------------------

import asyncio
import hmac
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import uvicorn  # noqa: E402
from fastmcp import FastMCP  # noqa: E402
from starlette.middleware import Middleware  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse, PlainTextResponse  # noqa: E402

from services.bridge.runner import Runner  # noqa: E402
from services.bridge.store import Store  # noqa: E402
from services.bridge import tools  # noqa: E402
from services.common.env import redacted, settings  # noqa: E402
from services.common.logging_setup import setup_logging  # noqa: E402

log = setup_logging("bridge")

MCP_PATH = "/mcp"
OPEN_PATHS = frozenset({"/", "/healthz"})
PLACEHOLDER_TOKENS = frozenset({"", "change-me"})

INSTRUCTIONS = (
    "Investigates questions about the bank's payments lakehouse with a recursive Prime Agent "
    "running on Cloudera AI. Investigations take minutes: start one, then poll its status and "
    "fetch its findings, which cite the SQL behind every number.")


class BearerAuth:
    """Pure ASGI bearer check, so streamed MCP responses are never buffered.

    Every path except OPEN_PATHS needs `Authorization: Bearer <BRIDGE_BEARER_TOKEN>`.
    With no token configured the server fails closed: every protected request is 401.
    """

    def __init__(self, app, token: str):
        self.app = app
        self.expected = f"Bearer {token}".encode() if token else None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in OPEN_PATHS:
            return await self.app(scope, receive, send)
        given = dict(scope.get("headers") or []).get(b"authorization", b"")
        if self.expected and hmac.compare_digest(given, self.expected):
            return await self.app(scope, receive, send)
        body = json.dumps({"error": "unauthorized"}).encode()
        await send({"type": "http.response.start", "status": 401, "headers": [
            (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
            (b"www-authenticate", b'Bearer realm="prime-agent-mcp"')]})
        await send({"type": "http.response.body", "body": body})


SHUTDOWN_GRACE_S = 30


@asynccontextmanager
async def lifespan(_server):
    yield {}
    # SIGTERM (docker stop, Workbench application restart): stop the running investigation's
    # sessions and record it as interrupted rather than leave it running in the daemon.
    if runner is not None:
        log.info("bridge stopping: interrupting %s", runner.current or "nothing")
        await asyncio.to_thread(runner.shutdown, SHUTDOWN_GRACE_S)


mcp = FastMCP("prime-agent-mcp", instructions=INSTRUCTIONS, lifespan=lifespan)
store: Store | None = None  # opened and migrated by create_app()
runner: Runner | None = None  # recovers the store and starts its worker in create_app()
tools.register(mcp, lambda: runner)


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


@mcp.custom_route("/", methods=["GET"])
async def root(_: Request) -> JSONResponse:
    s = settings()
    tool_names = sorted(t.name for t in await mcp.list_tools())
    return JSONResponse({
        "name": "prime-agent-mcp",
        "mcp_endpoint": MCP_PATH,
        "transport": "streamable-http (stateless, JSON responses)",
        "auth": "Authorization: Bearer <BRIDGE_BEARER_TOKEN>",
        "tools": tool_names,
        "store": {"schema": store.version(), "investigations": store.count()} if store else None,
        "runner": {"current": runner.current, "queued": runner.queued()} if runner else None,
        "deploy_target": s.deploy_target,
        "root_model": s.root_model.selector,
        "child_model": s.child_model.selector,
    })


def create_app():
    global store, runner
    s = settings()
    if store is None or store.path.parent != s.bridge_data_dir:
        store = Store(s.bridge_data_dir)
        log.info("job store %s: schema v%d (was v%d)", store.path, store.version(), store.migrated_from)
        runner = Runner(store)
        runner.recover()
        runner.start()
    if s.bridge_bearer_token in PLACEHOLDER_TOKENS:
        log.warning("BRIDGE_BEARER_TOKEN is %s; set a real token before exposing the bridge",
                    "unset, so /mcp rejects every request" if not s.bridge_bearer_token else "the placeholder")
    # Host/Origin checks off: on Workbench the app binds 127.0.0.1 behind a proxy and the
    # tunnel forwards a public Host header, so FastMCP's "auto" guard would reject both.
    # The bearer token is the boundary (a rebinding page cannot know it).
    return mcp.http_app(
        path=MCP_PATH, json_response=True, stateless_http=True, host_origin_protection=False,
        middleware=[Middleware(BearerAuth, token=s.bridge_bearer_token)])


if __name__ == "__main__":
    s = settings()
    log.info("starting bridge: %s", redacted(s))
    uvicorn.run(create_app(), host=s.bind_host, port=s.app_port, log_level="info")
