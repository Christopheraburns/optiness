# console

Streamlit investigation console. Burn-down epic H.

- `app.py`: entrypoint. Re-execs under the kernel venv, then runs Streamlit on
  `$BIND_HOST:$CDSW_APP_PORT`.
- `page.py`: the page. It lists investigations and shows the session tree (root → children),
  per-session status, elapsed time, tokens, code cells, SQL, and the cited evidence
  (SQL, rows, ms). It also renders `findings.md` as-is. Refreshes every `CONSOLE_REFRESH_S`.
- `model.py`: what the page shows (`Investigation`, `Session`, `Cell`, `OutputFile`,
  `Evidence`) and the `InvestigationSource` protocol. The page only talks to a source.
- `runs.py`: `FileRunSource`, the source that exists today. It reads
  `$AGENT_WORKDIR/runs/<run_id>/` (written by `scripts/run_investigation.py`) and the
  child transcripts under `$PRIME_AGENT_CODING_AGENT_DIR/session-artifacts/`. When the
  bridge's job store (F2) exists, a SQLite-backed source implements the same two methods.

Where each field comes from:

| Field | Source |
|---|---|
| question | `task.md`, the `Question:` line |
| root cells, streamed reply, child ids and status, end of turn | `events.jsonl` (ACP `session/update`; no timestamps) |
| child model, status, output file | `session-artifacts/*/sub-<id>/rlm-subagent.json`; the output file is the one named in the child's prompt |
| child cells, elapsed time, tokens | the child's transcript next to it |
| root elapsed time, tokens | `sessions/<root sid>.jsonl`, or the run id and file mtimes if there is no root transcript |
| cited SQL and rows | any `{"sql": …}` object in the child's output JSON (salvaged from invalid JSON too) |
| status | `done` = findings.md; `ended` = turn ended, no findings; `running` = files changed in the last 5 min; otherwise `stalled`. A child still `running` in a run that isn't running is shown as `abandoned` |

Child output files are free-form (each child picks its own shape), so "cited rows" is
whatever the child wrote. "SQL in cells" counts the SQL string literals in the code the
session actually ran.

Run locally against the host's runs:

```sh
AGENT_WORKDIR=$PWD/data/agent PRIME_AGENT_CODING_AGENT_DIR=$PWD/.prime/agent \
  CDSW_APP_PORT=8599 BIND_HOST=127.0.0.1 python3 services/console/app.py
```

Tests: `tests/test_console_runs.py`, which uses three real runs vendored in
`tests/fixtures/console/`.
