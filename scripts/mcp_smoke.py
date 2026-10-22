#!/usr/bin/env python
"""F7: run the bridge's full MCP tool sequence against a URL, the way Mistral Studio would.

    python3 scripts/mcp_smoke.py --url http://127.0.0.1:18080            # local stack
    python3 scripts/mcp_smoke.py --url https://<app>.<workbench-domain>  # Workbench
    make mcp-smoke [URL=...] [Q="..."] [N=1]

Checks the open routes and the auth boundary, then over MCP: initialize, tools/list,
list_investigations, start_investigation, get_investigation (polled until it ends),
get_findings (shape of the E10 contract), ask_followup, and cancel_investigation on
that follow-up so the sequence costs one investigation, not two. Standard library
only, so it runs on a laptop, in a Workbench session or in CI. The token comes from
--token, $BRIDGE_BEARER_TOKEN, or the repo's .env. Exit status 0 means every check passed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_Q = ("Smoke test: how many rows are in payment_transactions, and how many are declined "
             "(approved = false)? Use one subagent to count the declines.")
TOOLS = {"start_investigation", "get_investigation", "get_findings", "ask_followup",
         "list_investigations", "cancel_investigation"}
PROTOCOL = "2025-06-18"


class Smoke:
    def __init__(self, url: str, token: str, verbose: bool):
        self.url, self.token, self.verbose = url.rstrip("/"), token, verbose
        self.failures: list[str] = []
        self._id = 0

    # ---------------------------------------------------------------- output
    def check(self, ok: bool, what: str, detail: str = "") -> bool:
        print(f"{'PASS' if ok else 'FAIL'} {what}{f' ({detail})' if detail else ''}", flush=True)
        if not ok:
            self.failures.append(what)
        return ok

    def note(self, msg: str) -> None:
        print(f"     {msg}", flush=True)

    # ---------------------------------------------------------------- http
    def http(self, method: str, path: str, body: dict | None = None, token: str | None = None):
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
                   "MCP-Protocol-Version": PROTOCOL}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data, headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, r.headers.get("Content-Type", ""), r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type", ""), e.read().decode()

    def rpc(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        status, ctype, text = self.http("POST", "/mcp", {"jsonrpc": "2.0", "id": self._id, "method": method,
                                                         **({"params": params} if params else {})}, self.token)
        if status != 200:
            raise RuntimeError(f"{method}: HTTP {status}: {text[:300]}")
        if "text/event-stream" in ctype:  # tolerate an SSE-framed reply
            text = next(ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:"))
        msg = json.loads(text)
        if "error" in msg:
            raise RuntimeError(f"{method}: {msg['error']}")
        return msg["result"]

    def tool(self, name: str, **args) -> dict:
        r = self.rpc("tools/call", {"name": name, "arguments": args})
        if r.get("isError"):
            raise RuntimeError(f"{name}: {r['content'][0]['text']}")
        if self.verbose:
            self.note(f"{name} -> {json.dumps(r.get('structuredContent'))[:400]}")
        return r["structuredContent"]

    # ---------------------------------------------------------------- sequence
    def run(self, question: str, subagents: int, timeout: float, poll: float) -> int:
        status, _, body = self.http("GET", "/healthz")
        self.check(status == 200 and body.strip() == "ok", "GET /healthz", f"HTTP {status}")
        status, _, body = self.http("GET", "/")
        info = json.loads(body) if status == 200 else {}
        self.check(status == 200 and info.get("mcp_endpoint") == "/mcp", "GET /",
                   f"models {info.get('root_model')} / {info.get('child_model')}")
        status, _, _ = self.http("POST", "/mcp", {"jsonrpc": "2.0", "id": 0, "method": "tools/list"})
        self.check(status == 401, "POST /mcp without a token is refused", f"HTTP {status}")

        init = self.rpc("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                       "clientInfo": {"name": "mcp-smoke", "version": "1"}})
        self.check(init.get("serverInfo", {}).get("name") == "prime-agent-mcp", "initialize")
        listed = {t["name"]: t for t in self.rpc("tools/list")["tools"]}
        self.check(set(listed) == TOOLS, "tools/list has the six tools", ", ".join(sorted(listed)))
        self.check(all(t.get("description") and t.get("inputSchema") for t in listed.values()),
                   "every tool has a description and an input schema")
        before = self.tool("list_investigations", limit=5)
        self.check("investigations" in before, "list_investigations",
                   f"{len(before['investigations'])} recent")

        started = self.tool("start_investigation", question=question, subagents=subagents)
        inv = started["id"]
        self.check(inv.startswith("inv-") and started["status"] in ("queued", "running"), "start_investigation",
                   f"{inv}, {started['status']}")
        t0, last = time.time(), None
        while True:
            v = self.tool("get_investigation", id=inv)
            p = v["progress"]
            line = (f"{v['status']:<9} {time.time() - t0:5.0f}s  subagents {p['subagents_reported']}/"
                    f"{p['subagents_spawned']} reported, {p['subagents_reprompted']} re-prompted, "
                    f"{p['subagents_failed']} failed; root tool calls {p['root_tool_calls']}")
            if line.split("s ", 1)[-1] != last:
                self.note(line)
                last = line.split("s ", 1)[-1]
            if v["status"] not in ("queued", "running") or time.time() - t0 > timeout:
                break
            time.sleep(poll)
        self.check(v["status"] == "done", "get_investigation polled to completion",
                   f"{v['status']}, {v.get('outcome')}, {v.get('elapsed_s')} s")

        f = self.tool("get_findings", id=inv)
        evidence = [e for x in f["findings"] + f["ruled_out"] for e in x["evidence"]]
        self.check(f["ready"] and f["findings"], "get_findings returns findings",
                   f"{len(f['findings'])} findings, {len(f['ruled_out'])} ruled out, {len(evidence)} evidence items")
        self.check(bool(evidence) and all(e["sql"].strip() and e["session"] for e in evidence),
                   "every finding cites SQL and the session that ran it")
        self.check(bool(f.get("report_markdown")) and "```json" not in f["report_markdown"],
                   "written report returned without its JSON block", f"{len(f.get('report_markdown') or '')} chars")
        for x in f["findings"]:
            self.note(f"[{x['confidence']}] {x['claim'][:200]}")
        for x in f["ruled_out"]:
            self.note(f"[ruled out] {x['explanation'][:120]}")

        follow = self.tool("ask_followup", id=inv, question="Smoke follow-up: restate the total row count.",
                           subagents=1)
        self.check(follow["id"] != inv, "ask_followup starts a linked investigation", follow["id"])
        cancelled = self.tool("cancel_investigation", id=follow["id"])
        for _ in range(int(60 / max(poll, 1)) + 1):
            if cancelled["status"] == "cancelled":
                break
            time.sleep(min(poll, 5))
            cancelled = self.tool("get_investigation", id=follow["id"])
        self.check(cancelled["status"] == "cancelled" and cancelled.get("parent_id") == inv,
                   "cancel_investigation stops the follow-up", cancelled["status"])
        after = self.tool("list_investigations", limit=5)
        self.check({inv, follow["id"]} <= {i["id"] for i in after["investigations"]},
                   "list_investigations shows both")

        print(f"\n{'SMOKE PASS' if not self.failures else 'SMOKE FAIL: ' + '; '.join(self.failures)}"
              f"  (investigation {inv})", flush=True)
        return 0 if not self.failures else 1


def token_from_env() -> str:
    if os.environ.get("BRIDGE_BEARER_TOKEN"):
        return os.environ["BRIDGE_BEARER_TOKEN"]
    env = Path(__file__).resolve().parents[1] / ".env"
    if env.is_file():
        for line in env.read_text().splitlines():
            if line.startswith("BRIDGE_BEARER_TOKEN="):
                return line.split("=", 1)[1].strip()
    return ""


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--url", default="http://127.0.0.1:18080", help="Bridge base URL (no /mcp)")
    p.add_argument("--token", default=None, help="Bearer token (default: $BRIDGE_BEARER_TOKEN or .env)")
    p.add_argument("--question", default=DEFAULT_Q)
    p.add_argument("--subagents", type=int, default=1)
    p.add_argument("--timeout", type=float, default=1800, help="Seconds to wait for the investigation")
    p.add_argument("--poll", type=float, default=15, help="Seconds between get_investigation calls")
    p.add_argument("-v", "--verbose", action="store_true", help="Print every tool result")
    a = p.parse_args()
    token = a.token or token_from_env()
    if not token:
        raise SystemExit("no token: pass --token or set BRIDGE_BEARER_TOKEN")
    try:
        return Smoke(a.url, token, a.verbose).run(a.question, a.subagents, a.timeout, a.poll)
    except (RuntimeError, urllib.error.URLError) as e:
        print(f"FAIL {e}\n\nSMOKE FAIL", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
