"""prime-agent-mcp bridge. Placeholder until burn-down F1 replaces it with the
FastMCP server; it already honors the application contract (CDSW_APP_PORT,
BIND_HOST, /healthz, /) so the local stack and a Workbench application come
up green and the connector URL can be wired early."""
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

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import uvicorn  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse, PlainTextResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402

from services.common.env import redacted, settings  # noqa: E402
from services.common.logging_setup import setup_logging  # noqa: E402

log = setup_logging("bridge")


async def healthz(_: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


async def root(_: Request) -> JSONResponse:
    s = settings()
    return JSONResponse(
        {
            "name": "prime-agent-mcp",
            "status": "placeholder",
            "mcp_endpoint": "/mcp (not yet implemented: BURNDOWN F1)",
            "deploy_target": s.deploy_target,
            "root_model": s.root_model.selector,
            "child_model": s.child_model.selector,
        }
    )


app = Starlette(routes=[Route("/healthz", healthz), Route("/", root)])

if __name__ == "__main__":
    s = settings()
    log.info("starting bridge placeholder: %s", redacted(s))
    uvicorn.run(app, host=s.bind_host, port=s.app_port, log_level="info")
