#!/usr/bin/env bash
# Local entrypoint. Workbench does not use this; it launches the application
# script directly as user cdsw with CDSW_APP_PORT set. Locally we mimic that:
# ensure the home layout exists, render Prime Agent config from the
# environment when a renderer is present, then exec the requested command.
set -euo pipefail

export HOME="${HOME:-/home/cdsw}"
mkdir -p "$HOME/.prime/agent" "${TMPDIR:-/tmp}"

# Workbench apps bind to 127.0.0.1:$CDSW_APP_PORT behind the ingress. Locally
# we need 0.0.0.0 so Docker can publish the port. Services read BIND_HOST.
export CDSW_APP_PORT="${CDSW_APP_PORT:-8080}"
export BIND_HOST="${BIND_HOST:-0.0.0.0}"

# Config rendering (models.json, settings.json) is owned by the repo's
# scripts/render_config.py once it exists. Skip quietly until then.
if [ -f "$HOME/scripts/render_config.py" ]; then
  "${PRIME_AGENT_KERNEL_PYTHON}" "$HOME/scripts/render_config.py"
fi

exec "$@"
