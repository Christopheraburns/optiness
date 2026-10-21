# Burn-down: Optiness (Prime Agent on Cloudera AI Workbench for Mistral Studio)

Target: Evolve NY, 2026-10-27. Status values: Todo / Doing / Done / Blocked / Cut.
Estimates are hours. "Local" means Docker Desktop; "CAI" means Cloudera AI Workbench.

## Portability contract (applies to every item)

The same repo, image, entrypoints, and environment-variable names run in both places.
Only values differ. Nothing is edited to move from Local to CAI.

| Concern | Local (Docker Desktop) | Cloudera AI Workbench | Switch |
|---|---|---|---|
| Container image | Built from the Cloudera PBJ Python 3.11 runtime | Same image registered in the Runtime Catalog | none |
| App entrypoint | `app.py` per service, bound to `$BIND_HOST:$CDSW_APP_PORT` | Same, Workbench sets `CDSW_APP_PORT`, `BIND_HOST` defaults to 127.0.0.1 | env |
| Home / state | volume mounted at `/home/cdsw` | project filesystem at `/home/cdsw` | none |
| SQL engine | Apache Impala quickstart containers, Iceberg on local FS | CDW Impala virtual warehouse | `IMPALA_*` env |
| Metadata | `METADATA_BACKEND=stub` (or Atlas container) | `METADATA_BACKEND=atlas` | env |
| Policy | none (Impala local user) | Ranger on workload user | server-side, no code |
| Root model | Mistral API | Mistral API | none |
| Child model | Mistral API (Ministral) | Mistral API (Ministral) | none |
| Public URL for Studio | cloudflared tunnel to the bridge | Workbench application URL | connector setting |
| Data size | `DATA_SCALE=2000000` | `DATA_SCALE=50000000` | env |

## A. Repo and portability contract

| ID | Item | Done when | Est | Status |
|---|---|---|---|---|
| A1 | Create repo layout: `services/bridge`, `services/console`, `skills/`, `config/templates`, `data/`, `deploy/compose`, `deploy/cml`, `scripts/` | Tree committed with README | 1 | Done |
| A2 | Define `.env.example` with every variable in the contract table plus `MISTRAL_API_KEY`, `BRIDGE_BEARER_TOKEN`, `ROOT_MODEL`, `CHILD_MODEL`, `PRIME_AGENT_HOME`, `RLM_MAX_DEPTH`, `MAX_CHILDREN` | File reviewed, no service reads an undocumented var | 1 | Done |
| A3 | `scripts/render_config.py` renders `models.json` and `settings.json` from templates plus env at container start | Rendered files validated by `prime-agent config` locally | 2 | Done |
| A4 | Common `services/common/` module: env loading, bind host/port, Impala connection factory, logging | Used by bridge and console | 2 | Done |
| A5 | Makefile: `up`, `down`, `data`, `smoke`, `render`, `deploy-cml`, `screens` | Targets run clean on a fresh clone | 1 | Done |

## B. Runtime image (shared by Local and CAI)

| ID | Item | Done when | Est | Status |
|---|---|---|---|---|
| B1 | Dockerfile FROM Cloudera PBJ Workbench Python 3.11 runtime; confirm pull works from docker.repository.cloudera.com | Image builds | 1 | Done |
| B2 | Install pinned prime-agent v0.10.0 Linux release tarball into image, on PATH for user cdsw | `prime-agent --version` in container | 1 | Done |
| B3 | Install uv, impyla, thrift-sasl, pandas, pyarrow, mcp, fastmcp, streamlit, cloudflared (local only, optional) | `pip check` clean | 1 | Done |
| B4 | Pre-warm the Prime Agent kernel venv at build time so no egress is needed at runtime | Kernel boots offline in container | 2 | Done |
| B5 | Verify image runs as non-root cdsw with writable `/home/cdsw` and `$TMPDIR` for the daemon socket | Daemon starts, `prime-agent status` ok | 1 | Done |
| B6 | Push image to a registry Workbench can pull from; register in Runtime Catalog (admin) | Runtime selectable in a project | 1 | Done |

## C. Local stack (Docker Desktop)

| ID | Item | Done when | Est | Status |
|---|---|---|---|---|
| C1 | docker-compose: Impala quickstart (HMS, statestored, catalogd, impalad) with persistent volume | `impala-shell -q "select 1"` works | 2 | Done |
| C2 | Verify Iceberg v2 DDL on the local Impala version (CREATE TABLE ... STORED AS ICEBERG, partition transforms, metadata tables) | Test table created and queried | 1 | Done |
| C3 | Metadata backend switch: `stub` reads a JSON catalog file; `atlas` uses REST. Optional Atlas container documented but not required | Both backends pass the same unit test | 2 | Done |
| C4 | ~~Ollama container serving Ministral as a Cloudera AI Inference stand-in~~. Cut 2026-10-21: all models run on Mistral's endpoints; service removed from compose | n/a | 2 | Cut |
| C5 | cloudflared (or ngrok) compose service exposing the bridge over public HTTPS for Studio during development. Written and validated; run `make tunnel` when ready to publish | Public URL answers `/healthz` | 1 | Doing |
| C6 | Compose services for bridge and console using the runtime image, `.env`, `/home/cdsw` volume | `make up` brings everything green | 1 | Done |
| C7 | Local smoke: Prime Agent in container queries local Impala via the lakehouse skill | `make smoke` passes | 1 | Done |
| C8 | Local daemon on Apple Silicon. Rosetta crashes the Prime Agent daemon; QEMU breaks Impala (no AVX). Fixed: Docker Desktop on Rosetta for Impala, bridge/console on a native arm64 build of `image/Dockerfile` (`make image-local`: python:3.11-slim base, local only; `RUNTIME_IMAGE`/`RUNTIME_PLATFORM` in `.env`). Workbench keeps the Cloudera amd64 image, no new runtime needed. Verified 2026-10-21: daemon `current`, ACP root spawned two children, Impala up, `make test-impala` 8/8 | `prime-agent status` shows a running daemon and a probe `rlm.spawn` returns a handle, with Impala up | 2 | Done |

## D. Data

| ID | Item | Done when | Est | Status |
|---|---|---|---|---|
| D1 | Schema: Meridian's contract DDL vendored in `schema/`: `meridian_financial` (10 public tables, `payment_transactions` partitioned by `DAYS(transaction_ts)`) and `meridian_ground_truth` (2 private tables). Applied by `make schema`; never altered by us. `config/atlas-stub.json` is generated from the public DDL | `SHOW TABLES` lists all 12 on Local; DDL applies on CDW | 2 | Done |
| D2 | Root-cause spec: Meridian scenario `payment_routing_v1`. Routing config 4.12 on processor 3 (2-day 20% canary, then 80% with a 20% holdback) over-declines cross-border Mastercard ecommerce MCC 4722 (travel), 2026-09-11 to 2026-10-02; 4.13 restores it. Decoys: a 12-hour degradation on another processor, fraud-score declines, a routine 4.10→4.11 deploy on other processors, weekend dips. Spec and expected numbers live in the Meridian repo (`docs/m2_scenario_quality.md`, private `scenario_quality.json`), **never in this repo**: it is mounted at `/home/cdsw`, where the agent's Python runs | Expected numbers recorded on the Meridian side | 1 | Done |
| D3 | Generator: Meridian (separate repo), profile `config/demo.yaml`: 2M rows, 2026-08-04 to 2026-10-17, so the incident and recovery are in the past on demo day. Run `scripts/validate_scenario.py --generate --require-m2` → `data/demo-public` + `data/demo-private`. The 50M profile is D6 | 2M run passes every M2 acceptance check, in under 10 min | 3 | Done |
| D4 | Loader: `make data` runs `data/stage.py` on the host (hard-links one Meridian public run, `MERIDIAN_PUBLIC_DIR`, into `data/staging/meridian/<table>/`; refuses private or incomplete runs), then `data/load.py` in the bridge: external Parquet tables in `staging`, then `INSERT … SELECT` into the `meridian_financial` Iceberg tables. Order: dimensions, transactions one week per statement, `generation_runs` last as the completion marker. Resumable and idempotent; `REPLACE=1` swaps in a different run. Compose mounts the staging dir read-only into hms/catalogd/impalad only. Local quirk: emulated impalad wedges on multithreaded or codegen'd partitioned writes, so the loader sets `MT_DOP=0`, `DISABLE_CODEGEN=true` locally (reads are fine with defaults). CDW: same script with `--staging-uri s3a://…` | Every table's count equals its staged count; 2,000,000 transactions in 75 daily partitions; one `generation_runs` row, `run_status = complete`; re-run is a no-op | 2 | Done |
| D5 | Validation via SQL only, no agent, in two halves. Bankwide (this repo): `make validate-data` runs `scripts/validate_bankwide.py` through the agent's read-only path; it finds the lift from the data (no incident dates or causes in this repo) and writes `docs/data-validation.md` + `docs/daily-decline-rate.svg`. Answer key (Meridian repo): `scripts/reconcile_impala.py` rebuilds every private-evaluator group as SQL from the config and requires exact equality; SQL and output stay in Meridian's private dir. 2M result: bankwide 5/5 PASS (peak week +2.34 pp, final week +0.01 pp); reconciliation 23/23 groups exact | Bankwide charts in `docs/`; reconciliation noted on the Meridian side | 1 | Done |
| D6 | Run at 50M on CDW with Meridian `config/demo-50m.yaml` (needs D7; the Meridian job API refuses 50M, so use the CLI); record row counts, partition counts, table sizes | Numbers in `docs/dataset.md` | 2 | Todo |
| D7 | 50M readiness: Meridian `config/demo-50m.yaml` (same calendar and scenario as D3; 500k customers, 20k merchants, 80k terminals, 100k-row files) and a 5M timing run with those dimensions. Result: 5M passed M2 acceptance in 349 s, peak RSS 1.65 GB, 163 MB Parquet. Memory is flat in row count but scales with batch size (500k-row batches peaked at 4.8 GB), so keep 100k. Estimate for 50M: ~60 min on one core, ~1.7 GB RAM, ~1.6 GB Parquet | 5M passes M2 acceptance; time and memory recorded here | 1 | Done |

## E. Prime Agent configuration, skills, harness state

| ID | Item | Done when | Est | Status |
|---|---|---|---|---|
| E1 | `models.json` rendered by `scripts/render_config.py`: `mistral` provider with an explicit set checked against Mistral's `/v1/models` on 2026-10-21 (Large 3 `mistral-large-latest`, Large 4 `mistral-large-4` 1M ctx, Magistral Medium/Small, Ministral 14B/8B/3B) with correct reasoning flags (Large 3 is *not* a reasoning model), the only provider: every model call goes to Mistral's endpoints | `prime-agent model list` lists all | 1 | Done |
| E2 | `settings.json` rendered by `scripts/render_config.py`, keys checked against pa-core v0.10.0 `Settings`: `rlmMaxDepth`, skills dir, `subagentDefaultModel`; compaction via `reserveTokens` solved so the smallest root/child window compacts at `COMPACTION_THRESHOLD_TOKENS` (96k: caps per-call tokens against Mistral's 2M tokens/min); `retry` 5 x from 2 s (covers a one-minute rate-limit window); `allowedModels: ["mistral/*"]` (daemon-enforced only; verified not applied in `-p` print mode, check under `--mode rpc` in F3); optional `mcpServers.impala` when `IMPALA_MCP_URL` is set. Fixed: the old `compaction.thresholdPercent` is not a real key and was silently ignored | prime-agent loads settings leniently and never warns (unknown keys kept, wrong types dropped), so: `render_config` validates every rendered key and type and warns on stderr; renders with no warnings | 1 | Done |
| E3 | `skills/lakehouse`: `query(sql, max_rows)` → DataFrame with `attrs` sql/row_count/truncated/elapsed_ms, `tables`, `describe`, `profile`, `partitions`, `snapshots`, `explain`, `show`, `evidence()` (statement log for E10); read-only guard and connection reused from `services.common.impala`; table names validated before interpolation; pandas display capped. prime-agent only enables a Python skill the kernel can import and the kernel venv is root-owned, so skill `src` dirs go on `PYTHONPATH` (`.env`; Workbench project env); `render_config` warns on any unimportable skill | `make test-impala` 7/7 against local Impala in the kernel's Python; real agent turn on Mistral Large called it and returned 2,000,000 | 4 | Done |
| E4 | `skills/atlas`: `search(term)`, `entity(table)`, `lineage(table)`, `classifications(table)` over `services.common.metadata` (stub or Atlas REST, no branching in the skill); bare `table` names resolve to Atlas qualified names via search, so the cluster suffix never appears in agent code; unknown names raise `LookupError` with close matches; the private database is not resolvable. Both skills are async with blocking calls in a thread (prime-agent's `await <skill>` convention: a sync skill cost the model 10 tool calls to discover, async 4-7) and expose only their API in `dir()` | 7 tests pass on stub and fake Atlas REST; real Mistral Large turn returned the right classifications and lineage | 2 | Done |
| E5 | Investigation method in `config/templates/investigation-note.md`, rendered by `render_config` to `$AGENT_WORKDIR/AGENTS.md` (prime-agent's project-context layer: the whole text reaches the system prompt). Content: exact skill API, plan from metadata, symptom then control-group comparisons, aggregate in SQL, delegate by date range or one dimension with the `MAX_CHILDREN`/depth caps, every number from a tool result, cite SQL and row counts, findings plus rejected explanations. Kept answer-neutral (a test rejects any mention of the planted cause or its dimension). Global harness gets a 136-char prompt note pointing at it, because the harness digest shows only the first 140 chars of each entry (pa-core v0.10.0) | Note present in global harness state; method in the system prompt from `AGENT_WORKDIR`; the E4 catalog question now takes 1 tool call and 18k input tokens (was 4-10 calls, 45-73k, and one wrong answer) | 1 | Done |
| E6 | Subagent spec `partition-investigator` (`config/templates/partition-investigator.md`), seeded into global harness state by `render_config`; the root is pointed at it by the per-run task template (`config/templates/investigation-task.md`, sent by `scripts/run_investigation.py` / `make investigate`, the future F3 engine) rather than AGENTS.md, so children never see delegation rules. Spec fixes `model`/`thinking="off"`, half-open slices that cover the whole range with a sum check, `await` for every skill call (incl. `agent_message.send`), JSON result file, end-turn-after-spawn. Verified 2026-10-21 over ACP: root loaded the spec, spawned 2 Ministral 8B children that ran real SQL, wrote files and woke the root; findings in 100 s, root 6 tool calls (~104k tokens), children ~190-230k tokens each. Found and fixed in the spec: slice boundaries dropped one day (`<=` on a timestamp) | Spec present and referenced in a run | 1 | Done |
| E7 | Headless smoke: `prime-agent -p` answers a single-table question through the lakehouse skill. Verified 2026-10-21 from `AGENT_WORKDIR` on Mistral Large: row count and approval rate per `card_network` matched direct SQL exactly (2,000,000; Mastercard 94.91%, others ~96.5%), SQL and `attrs` evidence shown, 3 tool calls, 12 s, ~10k tokens/call. Found: `-p` ignores `defaultModel` in settings.json and runs `devstral-medium-latest`, so always pass `--provider/--model` (`smoke.sh` fixed; `run_investigation.py` already does). Also found: a wedged emulated impalad (D4 quirk) silently hangs agent queries with no timeout; restart impalad/catalogd and `make data` resumes | Correct answer with SQL shown | 1 | Done |
| E8 | Recursive smoke on the real question: `make investigate Q="…" N=3` (ACP, daemon-backed; E6 proved the mechanics with 2 children). Root on `ROOT_MODEL` plans from metadata, spawns children on `CHILD_MODEL` through the `partition-investigator` spec, sliced by date range or one dimension of its choosing; each child runs SQL and writes its slice file; root writes `findings.md`. The question is symptom-only (a decline-rate rise in `payment_transactions`), with no dates, cause or dimension in it. Depends on E9 if Ministral children don't call tools | Findings name scenario `payment_routing_v1`'s cause and window and reject its decoys, scored against Meridian's private answer key on the Meridian side (expected answers stay out of this repo); every number traces to child or root SQL in `events.jsonl`. Runs 2026-10-21 (Large 3 root, 3 x Ministral 8B), all FAIL: (1) root wrote its tool calls as text and ended its turn, runner waited out the timeout (runner now fails fast when the root is idle with no findings); (2) root never measured the series, read only `spec[:200]` and wrote its own child prompts (`BETWEEN` on timestamps), picked a weekend inside the incident as 'the anomaly', concluded 'no real increase', invented a 30% rate and an even-split assumption; (3) after the task template gained a plan-first step, `print(spec)` and numerator/denominator rule: root found the window from a daily query, but spent 8 calls on wrong column names and partition-string parsing, never queried the change/event tables, never split by a dimension; children returned a 0-row slice and 45k raw rows; 'cause' was an unsupported guess. Next: E9/L2 (larger child model) and a stronger root (Magistral Medium or Large 4) before more prompt work | 3 | Doing |
| E9 | Verify Magistral reasoning levels map through the Mistral provider; verify Ministral child tool calling through the Mistral provider | Both produce REPL tool calls. **Risk found in E3:** given a plain run-this-code prompt, `ministral-8b-latest` made no tool call and invented the output (twice); Mistral Large called the tool correctly. Check Ministral 14B/8B under the E6 spec; if it does not call tools reliably, children move to a larger model | 2 | Todo |
| E10 | Findings output contract: root ends with a JSON block `{findings:[{claim, confidence, evidence:[{session, sql, rows, elapsed_ms}]}]}` | Parser test passes | 1 | Todo |

## F. MCP bridge application (`services/bridge/app.py`)

| ID | Item | Done when | Est | Status |
|---|---|---|---|---|
| F1 | FastMCP Streamable HTTP server, stateless JSON, routes `/mcp`, `/healthz`, `/`, bearer-token middleware | `curl /healthz` 200, `/mcp` 401 without token | 2 | Todo |
| F2 | Job store (SQLite under `/home/cdsw/data`): `investigations`, `events`, `findings` | Schema migration on start | 2 | Todo |
| F3 | Runner: one daemon-backed session per investigation via `prime-agent --mode acp` (ACP JSON-RPC: `initialize`, `session/new` with `cwd=$AGENT_WORKDIR` so AGENTS.md loads, `session/prompt`; stream `session/update` events into the store). Not `--mode rpc`: verified 2026-10-21 that `-p`, `--mode json` and `--mode rpc` have no RLM child runtime, so no recursion. Track child spawn and settle events | Events visible for a smoke run | 4 | Todo |
| F4 | Tools: `start_investigation(question, scope)`, `get_investigation(id)`, `get_findings(id)`, `ask_followup(id, question)`, `list_investigations()`, `cancel_investigation(id)` | Each tool has schema and docstring | 3 | Todo |
| F5 | Findings extraction from the E10 contract into the `findings` table | Findings returned by `get_findings` | 1 | Todo |
| F6 | Process supervision: timeouts, orphan cleanup, restart-safe status, stdin-close settles the turn | Kill test leaves no zombies | 2 | Todo |
| F7 | MCP smoke client `scripts/mcp_smoke.py` runs the full tool sequence against a URL | Passes locally and on CAI | 1 | Todo |

## G. Mistral Studio

| ID | Item | Done when | Est | Status |
|---|---|---|---|---|
| G1 | Workspace, API key with connector scope | Key works in SDK | 0.5 | Todo |
| G2 | Register connector: name, server URL (tunnel for dev), `headers` with the bearer token, workspace visibility | Connector lists tools | 1 | Todo |
| G3 | Agent "Returns Investigator": Mistral Large or Magistral, system prompt that starts, polls, and reports with evidence | Agent created | 1 | Todo |
| G4 | End-to-end run from Studio playground against Local via tunnel | Findings appear in chat | 1 | Todo |
| G5 | Switch connector URL to the Workbench application URL and re-run | Findings appear in chat from CAI | 0.5 | Todo |
| G6 | Demo conversation script: prompts, expected replies, timing | Script in `docs/demo-script.md` | 1 | Todo |

## H. Investigation Console (`services/console/app.py`) and Agents View

| ID | Item | Done when | Est | Status |
|---|---|---|---|---|
| H1 | Streamlit console: investigations list, session tree, per-child SQL, rows scanned, elapsed, status, auto-refresh. Built 2026-10-21 (`services/console/`): page reads through an `InvestigationSource` (`model.py`); `FileRunSource` (`runs.py`) reads `$AGENT_WORKDIR/runs/<id>/` plus prime-agent child transcripts under `session-artifacts/` (events.jsonl has no timestamps and only the root's stream). Shows root → children tree, per-session model/status/elapsed/tokens, code cells with the SQL they ran and failures, child-cited evidence (SQL, rows, ms; salvaged from invalid JSON), findings.md as-is, `CONSOLE_REFRESH_S` refresh; statuses done/ended/running/stalled, children left `running` shown as abandoned. 18 tests over 3 vendored real runs (`tests/fixtures/console/`), incl. an AppTest render; verified on all 5 local runs incl. one live. The F2 SQLite store replaces `FileRunSource` behind the same two methods. Seen in the console, for E10/L2: child output JSON is free-form and sometimes invalid; Ministral 8B children call a nonexistent `agent_message` tool and retry `COUNT(*) FILTER` (unsupported in Impala) | Renders a live run | 4 | Done |
| H2 | Findings panel with evidence links; per-session HTML export via `prime-agent session export` | Export opens in browser | 2 | Todo |
| H3 | Agents View capture: run `prime-agent agents` in a Workbench Session terminal attached to the bridge daemon (same `PRIME_AGENT_HOME`); no separate app | Tree screenshot taken | 1 | Todo |
| H4 | Visual polish for screenshots: titles, Cloudera and Mistral naming, dark theme | Reviewed | 1 | Todo |

## I. Cloudera AI Workbench deployment

| ID | Item | Done when | Est | Status |
|---|---|---|---|---|
| I1 | Project from git, runtime from B6, env vars set at project and application level, API key | Session opens with image | 1 | Todo |
| I2 | Admin: enable "Allow applications to be configured with unauthenticated access" | Setting on | 0.5 | Todo |
| I3 | `deploy/cml/deploy.py` using cmlapi: create or update applications `prime-agent-mcp` and `investigation-console` with runtime, env, resources, subdomain | Idempotent re-run | 2 | Todo |
| I4 | External smoke: `/healthz` and F7 against the Workbench URL | Passes | 0.5 | Todo |
| I5 | CDW connection: workload user, HTTP transport, SSL, `IMPALA_*` values | E7 passes on CAI | 1 | Todo |

## J. Governance evidence

| ID | Item | Done when | Est | Status |
|---|---|---|---|---|
| J1 | Ranger read-only policy for the investigator user on the demo database; attempt an INSERT and capture the denial | Screenshot | 1 | Todo |
| J2 | Atlas lineage for `returns` and `transactions` after load; capture | Screenshot | 1 | Todo |
| J3 | ~~Cloudera AI Inference endpoint serving Ministral~~. Cut 2026-10-21: no Cloudera-hosted inference; model usage is shown from Mistral Studio instead (PLAN.md screenshot 9) | n/a | 2 | Cut |
| J4 | CDW Impala query history showing model-generated SQL from a run; capture | Screenshot | 0.5 | Todo |
| J5 | Continual Harness memory written by the agent during a run; capture | Screenshot | 0.5 | Todo |

## K. Demo preparation

| ID | Item | Done when | Est | Status |
|---|---|---|---|---|
| K1 | Pre-run the canonical investigation on CAI; keep investigation id and results warm | Results retrievable | 1 | Todo |
| K2 | Record a backup video of the full flow | Video file | 2 | Todo |
| K3 | Assemble screenshot pack per the checklist in PLAN.md section 6 | Folder complete | 1 | Todo |
| K4 | Two dry runs with timing; define live path and fallback path | Run sheet | 2 | Todo |
| K5 | Hygiene: rotate bearer token and API keys, tear down the tunnel, remove unauthenticated access if not needed after the event | Done after demo | 0.5 | Todo |

## L. FDE readiness (customer builds: nothing faked)

The Evolve demo is recorded and edited; Forward Deployed Engineers will run this for real in
customer environments. These items make it repeatable there. They follow the demo-critical
items and must not put the 2026-10-27 recording at risk.

| ID | Item | Done when | Est | Status |
|---|---|---|---|---|
| L1 | Budgets: per-investigation wall clock enforced by the runner/bridge with partial findings at the limit; per-child turn or token cap (check which prime-agent settings, e.g. `autonomous`, apply to spawned children); fan-out cap enforced, not just stated in AGENTS.md | A runaway run stops at its limit and still returns what it has | 3 | Todo |
| L2 | Child model reliability: measure tool-call rate and correctness of Ministral 8B/14B vs larger models under the E6 spec; pick the child model on evidence (E9 found Ministral 8B inventing results) | Chosen child model calls tools and returns correct numbers in 5 of 5 runs | 2 | Todo |
| L3 | Rate limits and cost: record tokens per minute and per agent from E8 runs; separate Mistral key or workspace for children if the 2M tokens/min budget is at risk | Peak tokens/min recorded; no 429s in 3 consecutive full runs | 1 | Todo |
| L4 | Workbench end-to-end on the real Cloudera runtime image (local dev uses a native arm64 build, C8): render_config at app start (Workbench ignores the image ENTRYPOINT), PYTHONPATH and AGENT_WORKDIR as project env, daemon-backed ACP run, skills against CDW | `make investigate`-equivalent run completes on Workbench | 2 | Todo |
| L5 | Customer portability: method, skills and spec stay customer-neutral; engagement specifics only in task templates; a short FDE runbook (setup, data load, schema, run, troubleshoot) | An FDE not on this project completes a run from the runbook | 2 | Todo |

## Totals

| Epic | Items | Est hours |
|---|---|---|
| A Repo and contract | 5 | 7 |
| B Runtime image | 6 | 7 |
| C Local stack | 8 | 12 |
| D Data | 7 | 12 |
| E Prime Agent config and skills | 10 | 17 |
| F MCP bridge | 7 | 15 |
| G Mistral Studio | 6 | 5 |
| H Console and Agents View | 4 | 8 |
| I Workbench deployment | 5 | 5 |
| J Governance evidence | 5 | 5 |
| K Demo prep | 5 | 6.5 |
| L FDE readiness | 5 | 10 |
| Total | 73 | 109.5 |

## Suggested order

Day 1: A1-A5, B1-B5, C1-C2, C6. Day 2: D1-D4, E1-E3, E7, C7. Day 3: E4-E6, E8-E10, C5.
Day 4: F1-F7, G1-G4. Day 5: H1-H4, B6, I1-I5, G5. Day 6: D6, J1-J5, K1. Day 7: K2-K4.

## Must-have for a credible demo

B1-B5, C1, C6, D1-D4, E1-E3, E7-E8, F1-F5, G2-G4, H1, I1-I4, J1-J2, K1-K3.
Everything else is Nice-to-have and can be Cut on Day 5 if behind.
