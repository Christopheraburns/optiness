#!/usr/bin/env bash
# Image self-test: proves the pieces the burn-down item B5 cares about.
set -euo pipefail
echo "prime-agent: $(prime-agent --version)"
echo "uv:          $(uv --version)"
echo "kernel py:   $(${PRIME_AGENT_KERNEL_PYTHON} --version)"
"${PRIME_AGENT_KERNEL_PYTHON}" - <<'PY'
import rlm, rlm.repl, impala.dbapi, fastmcp, pandas
print(f"rlm protocol {rlm.repl.PROTOCOL_VERSION}, pandas {pandas.__version__}, fastmcp ok, impyla ok")
PY
echo "home:        $HOME (writable: $([ -w "$HOME" ] && echo yes || echo no))"
echo "tmp:         ${TMPDIR:-/tmp} (writable: $([ -w "${TMPDIR:-/tmp}" ] && echo yes || echo no))"
echo "user:        $(id)"
