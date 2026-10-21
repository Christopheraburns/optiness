#!/usr/bin/env bash
# C7 smoke, run inside the bridge container (make smoke) or a Workbench session.
#   1. render config          2. Impala connectivity through services.common
#   3. Iceberg v2 DDL probe   4. Prime Agent headless run if MISTRAL_API_KEY is set
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PRIME_AGENT_KERNEL_PYTHON:-python}"

echo "== 1. render config"
"$PY" scripts/render_config.py | head -3

echo "== 2. impala connectivity"
"$PY" - <<'PYEOF'
from services.common.impala import run_query
from services.common.env import settings
s = settings().impala
print(f"[impala] {s.host}:{s.port} auth={s.auth_mechanism} http={s.use_http} ssl={s.use_ssl}")
r = run_query("SELECT version()", database="default")
print("[impala] version:", str(r.rows[0][0])[:80], f"({r.elapsed_ms} ms)")
r = run_query("SHOW DATABASES", database="default")
print("[impala] databases:", [row[0] for row in r.rows])
try:
    run_query("DROP TABLE nope", database="default")
    raise SystemExit("read-only guard FAILED to block DDL")
except Exception as e:
    print("[impala] read-only guard ok:", type(e).__name__)
PYEOF

echo "== 3. iceberg v2 probe"
"$PY" scripts/iceberg_check.py

echo "== 4. prime-agent headless"
if [ -n "${MISTRAL_API_KEY:-}" ]; then
  # -p ignores settings.json defaultModel (falls back to devstral), so name the model.
  prime-agent --print --provider "${ROOT_MODEL%%/*}" --model "${ROOT_MODEL#*/}" --thinking off \
    "Using the python_repl, run: from services.common.impala import run_query; print(run_query('SHOW DATABASES').rows). Reply with the database names only." \
    2>&1 | tail -5
else
  echo "[prime-agent] MISTRAL_API_KEY not set; skipping model run (BURNDOWN E7)"
fi
echo "== smoke PASS"
