# Prime Agent on Cloudera AI Workbench for Mistral Studio

Working plan for the Evolve NY main-stage demo (October 27, 2026).
Written 2026-10-20. Source material: PrimeAgent.pdf (Karten et al., arXiv 2608.23552)
and https://github.com/PrimeIntellect-ai/prime-agent (v0.10.0, Rust port).

## 1. What the paper and code describe

Prime Agent is an open-source harness for long-running agent work. Two ideas carry it:

1. Recursive Language Model (RLM). Every session owns a persistent IPython REPL. The model's
   only tool is `python_repl`. Everything else (bash, edits, MCP calls, subagents) is a Python
   function called from code. Large data stays in REPL variables (L2) instead of the prompt
   (L1). `rlm.spawn(prompt, name=, model=, thinking=)` creates a persistent child session with
   its own context and kernel. Results come back by agent-to-agent messages, not return values.
2. Continual Harness. Prompt notes, memories, executable skills, and subagent specs are typed,
   versioned state on disk (L3). `/refine` or `refine.run()` updates them from trajectory
   evidence with rollback.

Supporting machinery: a daemon owns sessions independent of the client; an Agents View shows
the session tree; autonomous mode, goals, and heartbeats drive long runs; a "factory" runs
declarative DAGs of subagents (fan-out with `foreach`, budgets, failure policy).

Results: ARC-AGI-3 30% to 95.5% (Opus 5), competitive long-context scores, an 85.5 hour
nanoGPT run, a 7 day Factorio run with 633 subagents. Noted failure: an agent persisted a
cheat as a reusable skill. The authors call for least-privilege interfaces, independent
validation, and auditable rollback.

Code facts that matter for us:
- Rust workspace (`pa-*` crates) plus a Python runtime (`prime-agent-runtime/src/rlm`).
  Linux x86_64 and aarch64 release binaries. Kernel needs Python 3.11, bootstrapped by `uv`.
- Modes: interactive TUI, `--print`, `--mode json`, `--mode rpc` (JSON commands on stdin,
  events on stdout: prompt, steer, follow_up, abort, get_state, fork, set_model...),
  `--mode acp` (Agent Client Protocol), `--mode daemon`.
- Providers: native Mistral provider (api.mistral.ai, MISTRAL_API_KEY, reasoning effort),
  generic `openai-completions`. `models.json` adds custom providers with baseUrl/api/headers.
- MCP client only. `mcpServers` setting supports `http` (url, headers, bearerTokenEnvVar)
  and `stdio`. The model calls `mcp.call_tool(server, tool, args)` from the REPL.
- Not a sandbox. Model-generated Python runs as the OS user.

## 2. Synergies

- Mistral Studio Connectors = remote MCP servers (name, server URL, headers, visibility).
  A Prime Agent MCP server hosted as a Workbench Application is exactly that shape.
- Prime Agent already speaks Mistral. Root on Mistral Large / Magistral, children on
  Ministral via `rlm.spawn(model="mistral/ministral-...")`. Every model call goes to Mistral's
  endpoints; no Cloudera-hosted inference is used in this demo.
- Workbench Applications are long-running, do not time out, and get a public HTTPS URL.
  kevinbtalbert/Mistral-Cloudera-MCPs already hosts an Impala MCP server this way.
- CDW Impala: official cloudera/iceberg-mcp-server (get_schema, execute_query) exists.
  Better: an in-kernel `lakehouse` skill using impyla so results land as DataFrames in L2.
- Iceberg metadata tables (snapshots, partitions, files) let the root plan partition-scoped
  child investigations.
- Atlas REST wraps cleanly as a skill (schema, lineage, classifications).
- Ranger enforces at query time through the workload user; children inherit it.
- Continual Harness memories/skills give a "the agent learned the schema" demo beat.
- Session JSONL + `session export` HTML = verifiable evidence for every finding.

## 3. Gaps

1. No MCP server in Prime Agent. We build a FastMCP bridge over `--mode rpc`.
2. Connector tool calls are synchronous; investigations run minutes. Use an async job API
   (start / status / result) and have the Studio agent poll or report partials.
3. Connector needs a public HTTPS URL. Workbench app with unauthenticated access plus a
   bearer header configured on the connector.
4. Tool calling on Ministral children must be verified through the Mistral provider.
5. Prime Agent kernel bootstrap downloads uv/Python; Workbench egress may block it.
   Pre-bake or test early.
6. Security: model-written Python runs in the app container. Use read-only workload user,
   Ranger read-only policies, isolated project.
7. Mistral Studio only sees tool results. The recursion is invisible without a console.
   Build an Investigation Console app (and a ttyd web terminal for the Agents View).
8. Need the 50M-row dataset with a planted root cause.
9. Prime Agent moves fast; pin v0.10.0.
10. Narrative: the abstract says Studio "orchestrates recursive investigations". In practice
    Studio orchestrates the top level and Prime Agent on Workbench does the recursion.

## 4. Architecture

Mistral Studio Agent (Mistral Large / Magistral)
  -> Connector (HTTPS, bearer header)
  -> Workbench Application "prime-agent-mcp" (FastMCP, Streamable HTTP, /mcp)
       tools: start_investigation, get_investigation, get_findings, ask_followup, list_investigations
       backend: one `prime-agent --mode rpc` process per investigation, events persisted
  -> Prime Agent root session (Mistral Large) with skills: lakehouse, atlas, investigation
       -> rlm.spawn children (Ministral via the Mistral API)
       -> each child runs Impala SQL through impyla (Ranger enforced) over Iceberg tables
  -> Workbench Application "investigation-console" (Streamlit/FastAPI over session JSONL)
  -> Workbench Application "agents-view" (ttyd running `prime-agent agents`)

## 5. Plan

The implementation plan is tracked as a burn-down list in BURNDOWN.md. It includes the
local Docker Desktop build path and the portability contract for moving to Cloudera AI
Workbench without code changes.

## 6. Screenshot checklist
1. Mistral Studio connector config pointing at the Workbench URL.
2. Mistral Studio agent chat starting an investigation and later reading findings.
3. Workbench Applications page with the three apps running.
4. Agents View showing root + Ministral children.
5. Investigation Console with SQL evidence and row counts.
6. Impala query history in CDW showing the model-generated SQL.
7. Atlas lineage for the returns table.
8. Ranger policy for the investigator user.
9. Mistral Studio usage view showing the root (Large) and Ministral child model calls.
10. Continual Harness memory written by the agent.
