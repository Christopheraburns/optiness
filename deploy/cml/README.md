# Cloudera AI Workbench applications (BURNDOWN I3)

Two Applications from the same project and runtime. Each Application's script is a
small launcher in this directory, not the service itself.

| Application | Script | Access | Serves |
|---|---|---|---|
| `prime-agent-mcp` | `deploy/cml/bridge.py` | Unauthenticated (I2): Mistral Studio calls it; `/mcp` requires `BRIDGE_BEARER_TOKEN` | MCP endpoint `/mcp`, `/healthz` |
| `investigation-console` | `deploy/cml/console.py` | Workbench login (default) | Streamlit console over run directories |

If the project contains the checkout in a subdirectory (for example `/home/cdsw/optiness`),
prefix the script path with it: `optiness/deploy/cml/bridge.py`.

## Why launchers

Workbench runs an Application's script as cells in an IPython kernel: there is no
`__file__`, `sys.argv` holds kernel flags, an event loop is already running (uvicorn
cannot start in it), and the image entrypoint (`image/entrypoint.sh`, which renders the
Prime Agent config) never runs. `services/common/workbench.py` therefore:

1. Finds the checkout (`OPTINESS_ROOT`, else the working directory, its parents or subdirectories).
2. Points path defaults (`SKILLS_DIR`, `AGENT_WORKDIR`, `BRIDGE_DATA_DIR`, `ATLAS_STUB_PATH`,
   `DATA_STAGING_DIR`, `PYTHONPATH` for the skills) at the checkout when it is not `$HOME`.
   Values set on the Application always win.
3. Bridge only: renders `~/.prime/agent/models.json` and `settings.json` (`scripts/render_config.py`).
4. Runs the service as a child process under `PRIME_AGENT_KERNEL_PYTHON`, streams its output
   to the Application log, forwards SIGTERM so the bridge can stop its run and sessions, and
   exits with the service's status (0 after a requested stop).

## Runtime

The Prime Agent runtime built from `image/Dockerfile` (PBJ Workbench base, B6). It must be a
**PBJ** runtime: in JupyterLab-editor runtimes the JupyterLab server occupies the application
port and the Application cannot bind. Spark is not needed.

## Environment

Set on the project (shared) or each Application. Secrets as Workbench environment values
visible only to the project's administrators, never in git.

| Variable | Bridge | Console | Value |
|---|:-:|:-:|---|
| `MISTRAL_API_KEY` | ✓ | | Mistral API key |
| `BRIDGE_BEARER_TOKEN` | ✓ | | Random; Mistral Studio's connector sends it |
| `ROOT_MODEL`, `CHILD_MODEL` | ✓ | | e.g. `mistral/mistral-large-4`, `mistral/mistral-medium-latest` |
| `ROOT_THINKING`, `CHILD_THINKING` | ✓ | | `off` (see `scripts/render_config.py`) |
| `IMPALA_HOST`, `IMPALA_PORT` | ✓ | | CDW coordinator host, `443` |
| `IMPALA_AUTH_MECHANISM`, `IMPALA_USE_HTTP`, `IMPALA_HTTP_PATH`, `IMPALA_USE_SSL` | ✓ | | `LDAP`, `true`, `cliservice`, `true` |
| `IMPALA_USER`, `IMPALA_PASSWORD` | ✓ | | **The Ranger-restricted agent login only** (see below) |
| `METADATA_BACKEND` | ✓ | | `stub` until Atlas is wired (C3) |
| `BRIDGE_PUBLIC_URL` | ✓ | ✓ | The bridge Application's URL. The console starts, watches and stops runs through it |
| `BRIDGE_BEARER_TOKEN` | ✓ | ✓ | Also used by the console, server-side only; never the `change-me` placeholder on Workbench |
| `OPTINESS_ROOT` | opt. | opt. | Only if the launcher cannot find the checkout |

Not needed on Workbench: `CDSW_APP_PORT`, `BIND_HOST`, `DEPLOY_TARGET` (detected), and the
local-only `CLOUDFLARE_TUNNEL_TOKEN`, `BRIDGE_PORT`, `CONSOLE_PORT`, `IMPALA_MEM_LIMIT`.

## Safety before the first investigation

Starting the Applications launches no agents: the bridge starts the Prime Agent daemon idle
and waits for `start_investigation`; the console only reads. The agent touches CDW only when
an investigation runs, with the `IMPALA_*` login. Until a Ranger-restricted agent login exists
(denied `meridian_ground_truth` and `meridian_private_staging`), leave `IMPALA_USER` and
`IMPALA_PASSWORD` unset and do not register the Studio connector (G2/G3).

Keep Meridian in a **separate project**: the agent's Python runs over this project's
filesystem, and Meridian's private output is the answer key.

## Verified locally (2026-10-22)

`prime-agent-runtime:0.10.0-local-arm64`, checkout mounted read-only at `/home/cdsw/optiness`,
working directory `/home/cdsw`, scripts executed as cells inside a running event loop with
kernel `sys.argv`: bridge rendered its config and answered `/healthz` and `/` (six tools) on
`127.0.0.1:8100`; SIGTERM produced the bridge's graceful shutdown and status 0, with no
service process left; console answered HTTP 200. Not yet verified on Workbench (I4).

## Still to do (rest of I3)

`deploy/cml/deploy.py` with `cmlapi` to create or update both Applications idempotently.
Until then, create them in the Workbench UI with the scripts and variables above.
