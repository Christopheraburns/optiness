"""Investigation Console server: a read-only JSON API over an InvestigationSource, plus the
single-page front end in ./static. Replaces the Streamlit page (BURNDOWN H1/H4).

Routes
  GET /                              the app (static/index.html)
  GET /static/...                    front end, vendored libraries, brand files
  GET /api/system                    deployment facts for the header and architecture view (no secrets)
  GET /api/investigations            every run, newest first, with summary stats
  GET /api/investigations/{run_id}   one run: session tree, cells, SQL, outputs, findings
  GET /healthz                       liveness
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from services.common.env import settings
from services.console.model import Investigation, InvestigationSource, Session
from services.console.runs import FileRunSource

STATIC = Path(__file__).resolve().parent / "static"
RESULT_CHARS = 6_000   # a cell's printed result, enough for a result table; the full text stays on disk
CODE_CHARS = 20_000

# Display names for the models the demo selects (ids as Mistral's API reports them).
MODEL_NAMES = {
    "mistral-large-4": "Mistral Large 4",
    "mistral-large-latest": "Mistral Large 3",
    "mistral-medium-latest": "Mistral Medium 3.5",
    "magistral-medium-latest": "Magistral Medium",
    "magistral-small-latest": "Magistral Small",
    "ministral-8b-latest": "Ministral 8B",
}


def model_name(model: str) -> str:
    model_id = model.rsplit("/", 1)[-1]
    return MODEL_NAMES.get(model_id, model_id.replace("-", " ").title() if model_id else "")


def iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def stats(inv: Investigation) -> dict[str, Any]:
    sessions = list(inv.root.walk())
    cells = [c for s in sessions for c in s.cells]
    children = inv.root.children
    contract = inv.findings or {}
    return {
        "queries": sum(len(c.sql) for c in cells),
        "cells": len(cells),
        "failed_cells": sum(not c.ok for c in cells),
        "tokens": sum(s.tokens or 0 for s in sessions) or None,
        "agents": len(sessions),
        "children_by_status": dict(Counter(c.status or "unknown" for c in children)),
        "root_model": model_name(inv.root.model),
        "child_models": dict(Counter(model_name(c.model) for c in children if c.model)),
        "findings": len(contract.get("findings") or []),
        "ruled_out": len(contract.get("ruled_out") or []),
        "evidence": sum(len(f.get("evidence") or []) for f in (contract.get("findings") or []) + (contract.get("ruled_out") or [])
                        if isinstance(f, dict)),
    }


def summary_json(inv: Investigation) -> dict[str, Any]:
    s = inv.summary
    return {
        "run_id": s.run_id, "question": s.question, "status": s.status,
        "started": iso(s.started), "ended": iso(s.ended), "elapsed_s": s.elapsed_s,
        "children": s.children, "has_findings": s.has_findings, **stats(inv),
    }


def session_json(sess: Session) -> dict[str, Any]:
    out = sess.output
    return {
        "id": sess.id, "name": sess.name, "role": sess.role, "status": sess.status,
        "model": sess.model, "model_name": model_name(sess.model),
        "started": iso(sess.started), "ended": iso(sess.ended), "elapsed_s": sess.elapsed_s,
        "tokens": sess.tokens, "rows": sess.rows,
        "cells": [{
            "code": c.code[:CODE_CHARS], "sql": c.sql, "ok": c.ok, "duration_ms": c.duration_ms,
            "result": c.result[:RESULT_CHARS], "result_truncated": len(c.result) > RESULT_CHARS,
        } for c in sess.cells],
        "output": None if out is None else {
            "name": out.name, "parse_error": out.parse_error,
            "evidence": [{"sql": e.sql, "rows": e.row_count, "elapsed_ms": e.elapsed_ms, "path": e.path} for e in out.evidence],
        },
        "children": [session_json(c) for c in sess.children],
    }


def investigation_json(inv: Investigation) -> dict[str, Any]:
    return {
        **summary_json(inv),
        "root": session_json(inv.root),
        "findings_contract": inv.findings,
        "findings_md": inv.findings_md,
        "root_reply": inv.root_reply,
        "other_outputs": [{"name": o.name, "parse_error": o.parse_error} for o in inv.unattributed],
    }


def system_json() -> dict[str, Any]:
    s = settings()
    warehouse = s.impala
    return {
        "deploy_target": s.deploy_target,
        "root_model": model_name(s.root_model.model),
        "child_model": model_name(s.child_model.model),
        "max_children": s.max_children,
        "warehouse": {
            "configured": bool(warehouse.host),
            "host": warehouse.host, "database": warehouse.database,
            "transport": "http" if warehouse.use_http else "binary", "tls": warehouse.use_ssl,
        },
        "metadata_backend": s.atlas.backend,
        "bridge_url": s.bridge_public_url or None,
    }


def create_app(source: InvestigationSource | None = None) -> Starlette:
    @lru_cache(maxsize=1)
    def runs() -> InvestigationSource:
        if source is not None:
            return source
        s = settings()
        return FileRunSource(s.agent_workdir / "runs", s.agent_dir)

    async def index(request):
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    async def healthz(request):
        return PlainTextResponse("ok")

    async def system(request):
        return JSONResponse(system_json())

    async def investigations(request):
        out = []
        for summary in runs().list_investigations():
            inv = runs().get_investigation(summary.run_id)
            if inv:
                out.append(summary_json(inv))
        return JSONResponse(out)

    async def investigation(request):
        run_id = request.path_params["run_id"]
        if not re.fullmatch(r"inv-\d{8}-\d{6}", run_id):
            return JSONResponse({"error": "not a run id"}, status_code=400)
        inv = runs().get_investigation(run_id)
        if inv is None:
            return JSONResponse({"error": f"no investigation {run_id}"}, status_code=404)
        return JSONResponse(investigation_json(inv))

    return Starlette(routes=[
        Route("/", index),
        Route("/healthz", healthz),
        Route("/api/system", system),
        Route("/api/investigations", investigations),
        Route("/api/investigations/{run_id}", investigation),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ])
