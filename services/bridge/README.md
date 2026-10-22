# bridge

FastMCP bridge Mistral Studio calls. Burn-down epic F.

| Route | Auth | Purpose |
|---|---|---|
| `/mcp` | `Authorization: Bearer $BRIDGE_BEARER_TOKEN` | MCP over Streamable HTTP, stateless, JSON responses |
| `/healthz` | none | liveness (`ok`) |
| `/` | none | name, endpoint, registered tools, models |

- No token configured means fail closed: every `/mcp` request is 401. The placeholder
  `change-me` logs a warning at start; set a real token before `make tunnel` or Workbench.
- Host/Origin checks are off on purpose: Workbench and the tunnel both proxy with a public
  Host header. The bearer token is the boundary.
- Job store (F2): `store.py`, SQLite at `$BRIDGE_DATA_DIR/bridge.db`, migrated at start.
  Rollback journal on purpose (network filesystem on Workbench); the bridge is the only writer.
- Runner (F3): `runner.py` queues investigations and runs them one at a time through the shared
  engine `services/common/investigation.py`; events, status and findings go to the store.
- Supervision (F6): cancel, timeout, SIGTERM and restarts never leave agents running; `make kill-test`
  checks it against the live stack (a few minutes of model time).
- Tools (F4): `tools.py` registers `start_investigation`, `get_investigation`, `get_findings`,
  `ask_followup`, `list_investigations`, `cancel_investigation`. Start returns at once; callers poll.
- Tests: `make test-bridge` (bridge + tools, in the agent kernel's Python); store and runner tests run under pytest.
