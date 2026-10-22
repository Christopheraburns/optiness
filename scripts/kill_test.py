#!/usr/bin/env python
"""F6 kill test against the live local bridge (`make kill-test`): cancel, graceful restart, hard kill.

Each scenario starts a trivial one-subagent investigation through the MCP tools, waits for
the subagent, interrupts the run, then checks: the status recorded, no daemon session still
working (checked twice, 20 s apart: a restored session can resume), no `--mode acp`
process, no zombie. Costs a minute or two of model time per scenario. Usage:
`python3 scripts/kill_test.py [A] [B] [C]` from the repo root, with the stack up.
"""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ENV = Path(__file__).resolve().parents[1] / ".env"
TOKEN = next(l.split("=", 1)[1] for l in ENV.read_text().splitlines() if l.startswith("BRIDGE_BEARER_TOKEN="))
URL = "http://127.0.0.1:18080"
Q = "Kill test: how many rows are in payment_transactions? Use one subagent to count them."


def mcp(name, **args):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": name, "arguments": args}}).encode()
    req = urllib.request.Request(URL + "/mcp", body, {
        "Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"})
    r = json.load(urllib.request.urlopen(req, timeout=60))["result"]
    if r.get("isError"):
        raise RuntimeError(r["content"][0]["text"])
    return r["structuredContent"]


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout


def wait_bridge():
    for _ in range(60):
        try:
            urllib.request.urlopen(URL + "/healthz", timeout=2)
            return
        except Exception:
            time.sleep(1)
    raise SystemExit("bridge did not come back")


def start_and_wait_for_child():
    inv = mcp("start_investigation", question=Q, subagents=1)["id"]
    for _ in range(90):
        v = mcp("get_investigation", id=inv)
        if v["progress"]["subagents_spawned"] >= 1 or v["status"] not in ("queued", "running"):
            break
        time.sleep(2)
    print(f"  {inv}: status {v['status']}, subagents {v['progress']['subagents_spawned']}, "
          f"after {v['elapsed_s']}s", flush=True)
    return inv


def check(inv, label):
    time.sleep(3)
    v = mcp("get_investigation", id=inv)
    events = [e["kind"] for e in v["progress"]["recent_events"]]
    busy = []
    for _ in range(2):  # again after 20 s: a restored session can resume working
        out = sh("docker exec optiness-bridge prime-agent sessions --json")
        if not out.strip():
            busy = ["<daemon unreachable>"]
            break
        rows = json.loads(out).get("sessions", [])
        busy = [r["id"] for r in rows if r.get("isStreaming") or r.get("activity") not in (None, "idle")]
        if busy:
            break
        time.sleep(20)
    procs = sh("docker exec optiness-bridge ps -eo pid,ppid,stat,args")
    zombies = [l for l in procs.splitlines()[1:] if l.split()[2].startswith("Z")]
    acp = [l for l in procs.splitlines() if "--mode acp" in l]
    print(f"  [{label}] status={v['status']} outcome={v['outcome']!r}")
    print(f"  [{label}] recent events: {events}")
    print(f"  [{label}] busy daemon sessions: {busy}  acp processes: {len(acp)}  zombies: {zombies}")
    return v["status"], busy, acp, zombies


def scenario_a():
    print("A. cancel_investigation mid-run", flush=True)
    inv = start_and_wait_for_child()
    mcp("cancel_investigation", id=inv)
    for _ in range(30):
        if mcp("get_investigation", id=inv)["status"] != "running":
            break
        time.sleep(1)
    return check(inv, "A"), "cancelled"


def scenario_b():
    print("B. graceful restart (SIGTERM) mid-run", flush=True)
    inv = start_and_wait_for_child()
    t0 = time.time()
    sh("docker restart optiness-bridge")
    print(f"  restart took {time.time() - t0:.0f}s", flush=True)
    wait_bridge()
    print("  shutdown log:", sh("docker logs optiness-bridge 2>&1 | grep -E 'bridge stopping' | tail -1").strip())
    return check(inv, "B"), "failed"


def scenario_c():
    print("C. hard kill (SIGKILL) mid-run, then start", flush=True)
    inv = start_and_wait_for_child()
    sh("docker kill -s KILL optiness-bridge && docker start optiness-bridge")
    wait_bridge()
    time.sleep(5)
    print("  recover log:", sh("docker logs optiness-bridge 2>&1 | grep -E 'recovered|daemon did not' | tail -2").strip())
    return check(inv, "C"), "failed"


only = sys.argv[1:] or ["A", "B", "C"]
results = {k: {"A": scenario_a, "B": scenario_b, "C": scenario_c}[k]() for k in only}
ok = all(status == want and not busy and not acp and not z
         for (status, busy, acp, z), want in results.values())
print("KILL TEST", "PASS" if ok else "FAIL", only)
sys.exit(0 if ok else 1)
