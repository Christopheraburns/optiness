"""Investigation Console server: a read-only JSON API over an InvestigationSource, plus the
single-page front end in ./static. Replaces the Streamlit page (BURNDOWN H1/H4).

Routes
  GET /                              the app (static/index.html)
  GET /static/...                    front end, vendored libraries, brand files
  GET /api/system                    deployment facts for the header and architecture view (no secrets)
  GET /api/investigations            every run, newest first, with summary stats
  GET /api/investigations/{run_id}   one run: session tree, cells, SQL, outputs, findings
  GET /api/investigations/{run_id}/timeline   the run as a timeline: queries, events, sessions, hypotheses
  GET /api/overview[?refresh=1]      bankwide payment health from the warehouse, with the detected anomaly
  GET /api/dataset[?refresh=1]       the lakehouse: tables, rows, format, partitions, relationships
  GET /api/architecture              each part of the system and its live status, with the latest run's numbers
  GET /api/launch                    what New investigation needs: presets, limits, models, bridge state
  POST /api/launch/preview           the exact prompt the root agent would receive
  POST /api/investigations           start a run through the bridge (start_investigation)
  GET /api/live/{run_id}             the bridge's progress for a run (get_investigation)
  POST /api/investigations/{run_id}/cancel   stop a run (cancel_investigation)
  GET /healthz                       liveness

POSTs require the header X-Console-Action: a cross-site page cannot set it without a CORS
preflight this server never grants, so other sites cannot start runs through a signed-in
browser, whatever host the Workbench proxy presents.
"""
from __future__ import annotations

import asyncio
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
from services.common.findings import report_text
from services.console import architecture, bridge, dataset, launch, overview, timeline
from services.console.model import Investigation, InvestigationSource, Session
from services.console.runs import FileRunSource

STATIC = Path(__file__).resolve().parent / "static"
RUN_ID = re.compile(r"inv-\d{8}-\d{6}")
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
        "report_md": report_text(inv.findings_md),
        "root_reply": inv.root_reply,
        "other_outputs": [{"name": o.name, "parse_error": o.parse_error} for o in inv.unattributed],
    }


CONSOLE_CONFIG = Path(__file__).resolve().parents[2] / "config" / "console.json"


def console_config() -> dict[str, Any]:
    """Engagement wording for the console (config/console.json); empty values are not shown."""
    try:
        import json
        data = json.loads(CONSOLE_CONFIG.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


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
        "closing_message": console_config().get("closing_message") or None,
        "can_start": not bridge.configured(),
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
        if not RUN_ID.fullmatch(run_id):
            return JSONResponse({"error": "not a run id"}, status_code=400)
        inv = runs().get_investigation(run_id)
        if inv is None:
            return JSONResponse({"error": f"no investigation {run_id}"}, status_code=404)
        return JSONResponse(investigation_json(inv))

    def action(handler):
        """POST guard: same-site requests from the console carry X-Console-Action; JSON bodies only."""
        async def guarded(request):
            if request.headers.get("x-console-action") != "1":
                return JSONResponse({"error": "missing X-Console-Action header"}, status_code=403)
            try:
                body = await request.json() if await request.body() else {}
            except ValueError:
                return JSONResponse({"error": "body must be JSON"}, status_code=400)
            if not isinstance(body, dict):
                return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
            try:
                return await handler(request, body)
            except ValueError as e:
                return JSONResponse({"error": str(e)}, status_code=400)
            except bridge.BridgeError as e:
                return JSONResponse({"error": str(e)}, status_code=502)
        return guarded

    async def run_timeline(request):
        run_id = request.path_params["run_id"]
        if not RUN_ID.fullmatch(run_id):
            return JSONResponse({"error": "not a run id"}, status_code=400)
        inv = runs().get_investigation(run_id)
        if inv is None:
            return JSONResponse({"error": f"no investigation {run_id}"}, status_code=404)
        return JSONResponse(timeline.build(inv))

    async def payment_health(request):
        if not settings().impala.host:
            return JSONResponse({"available": False, "reason": "No warehouse is configured for the console (IMPALA_HOST)."})
        try:
            data = await asyncio.to_thread(overview.overview, request.query_params.get("refresh") == "1")
        except Exception as e:  # noqa: BLE001 - surface the warehouse's own message, never secrets
            return JSONResponse({"available": False, "reason": f"The warehouse query failed: {type(e).__name__}: {str(e)[:300]}"})
        return JSONResponse({"available": True, **data})

    async def lakehouse(request):
        if not settings().impala.host:
            return JSONResponse({"available": False, "reason": "No warehouse is configured for the console (IMPALA_HOST)."})
        try:
            data = await asyncio.to_thread(dataset.dataset, request.query_params.get("refresh") == "1")
        except Exception as e:  # noqa: BLE001 - surface the warehouse's own message, never secrets
            return JSONResponse({"available": False, "reason": f"The warehouse query failed: {type(e).__name__}: {str(e)[:300]}"})
        return JSONResponse({"available": True, "database": settings().impala.database, **data})

    async def system_map(request):
        s = settings()
        wh, hs = await asyncio.gather(asyncio.to_thread(architecture.warehouse), asyncio.to_thread(architecture.harness))
        summaries = runs().list_investigations()
        # The harness panel shows a real run: the latest with findings and parallel workers, else any with findings.
        latest = next((x for x in summaries if x.has_findings and x.children >= 2), None) or \
            next((x for x in summaries if x.has_findings), None)
        latest_json = None
        if latest:
            inv = runs().get_investigation(latest.run_id)
            if inv:
                tl = timeline.build(inv)
                latest_json = {**summary_json(inv),
                               "reports": sum(1 for e in tl["events"] if e["kind"] == "report"),
                               "reprompts": sum(1 for e in tl["events"] if e["kind"] == "notice"),
                               "evidence_matched": sum(1 for h in tl["hypotheses"] for e in h["evidence"] if e["at"] is not None)}
        return JSONResponse({
            "console": {"state": "up", "deploy_target": s.deploy_target},
            "studio": architecture.studio(), "harness": hs, "warehouse": wh,
            # The models the bridge actually runs with; the console's own settings only if it is down.
            "models": {"root": model_name(hs.get("root_model") or s.root_model.model),
                       "child": model_name(hs.get("child_model") or s.child_model.model),
                       "source": "bridge" if hs.get("root_model") else "console settings",
                       "provider": "Mistral API", "max_children": s.max_children},
            "runs": {"total": len(summaries), "running": sum(1 for x in summaries if x.status == "running")},
            "latest": latest_json,
        })

    async def launch_info(request):
        s = settings()
        return JSONResponse({
            "presets": launch.presets(), "max_children": s.max_children,
            "root_model": model_name(s.root_model.model), "child_model": model_name(s.child_model.model),
            "bridge": await asyncio.to_thread(bridge.status),
        })

    async def launch_preview(request, body):
        question = body.get("question") or ""
        subagents = body.get("subagents", 3)
        if not isinstance(subagents, int) or isinstance(subagents, bool):
            raise ValueError("Workers must be a whole number.")
        return JSONResponse({"prompt": launch.preview(question or "<your question>", body.get("scope"), subagents)})

    async def start(request, body):
        if bridge.configured():
            raise bridge.BridgeError("; ".join(bridge.configured()))
        question, scope, subagents = launch.validate(body.get("question"), body.get("scope"), body.get("subagents"))
        args = {"question": question, "subagents": subagents, **({"scope": scope} if scope else {})}
        started = await asyncio.to_thread(bridge.call, "start_investigation", **args)
        return JSONResponse({"id": started.get("id"), "status": started.get("status"),
                             "queue_position": started.get("queue_position")}, status_code=201)

    async def live(request):
        run_id = request.path_params["run_id"]
        if not RUN_ID.fullmatch(run_id):
            return JSONResponse({"error": "not a run id"}, status_code=400)
        if bridge.configured():
            return JSONResponse({"available": False, "reason": "; ".join(bridge.configured())})
        try:
            view = await asyncio.to_thread(bridge.call, "get_investigation", id=run_id)
        except bridge.BridgeError as e:
            return JSONResponse({"available": False, "reason": str(e)})
        return JSONResponse({"available": True, **view})

    async def cancel(request, body):
        run_id = request.path_params["run_id"]
        if not RUN_ID.fullmatch(run_id):
            raise ValueError("not a run id")
        result = await asyncio.to_thread(bridge.call, "cancel_investigation", id=run_id)
        return JSONResponse({"id": run_id, "status": result.get("status")})

    return Starlette(routes=[
        Route("/", index),
        Route("/healthz", healthz),
        Route("/api/system", system),
        Route("/api/investigations", investigations),
        Route("/api/investigations", action(start), methods=["POST"]),
        Route("/api/investigations/{run_id}", investigation),
        Route("/api/investigations/{run_id}/cancel", action(cancel), methods=["POST"]),
        Route("/api/investigations/{run_id}/timeline", run_timeline),
        Route("/api/launch", launch_info),
        Route("/api/overview", payment_health),
        Route("/api/dataset", lakehouse),
        Route("/api/architecture", system_map),
        Route("/api/launch/preview", action(launch_preview), methods=["POST"]),
        Route("/api/live/{run_id}", live),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ])
