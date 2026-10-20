# Prime Agent runtime image

Builds one container image used both by Docker Desktop (local) and the
Cloudera AI Workbench Runtime Catalog.

## Build

    # Intel or Workbench target
    docker build -t prime-agent-runtime:0.10.0 -f image/Dockerfile image

    # Apple Silicon local runs (the base image is amd64 only; this runs under
    # emulation, so use it for correctness checks rather than speed)
    docker build --platform linux/amd64 -t prime-agent-runtime:0.10.0 -f image/Dockerfile image

## Verify

    docker run --rm prime-agent-runtime:0.10.0 healthcheck.sh

## Layout inside the image

| Path | Purpose |
|---|---|
| /opt/prime-agent/pkg | Release tarball contents: binary, prime-agent-runtime sidecar, bundled skills |
| /opt/prime-agent/bin | prime-agent symlink, uv, entrypoint.sh, healthcheck.sh |
| /opt/prime-agent/kernel-venv | Pre-warmed Python 3.11 venv used by the RLM kernel, the bridge, and the console |
| /home/cdsw | HOME. Workbench mounts the project here; locally a named volume |
| /home/cdsw/.prime/agent | Prime Agent settings, models.json, sessions, harness state |

## Environment contract

| Variable | Set in image | Meaning |
|---|---|---|
| PRIME_AGENT_KERNEL_PYTHON | yes | Skips the first-run uv bootstrap; points at the pre-warmed venv |
| PRIME_AGENT_KERNEL_VENV | yes | Same venv, for code paths that read the directory |
| PRIME_AGENT_TELEMETRY, DO_NOT_TRACK | yes | Telemetry off |
| CDSW_APP_PORT | Workbench sets it; entrypoint defaults to 8080 locally | Port an application must listen on |
| BIND_HOST | entrypoint defaults to 0.0.0.0 locally; services default to 127.0.0.1 | Interface to bind |

## Registering in Workbench

Push the image to a registry the Workbench can pull from, then add it under
Site Administration > Runtimes with the full image path. The
`com.cloudera.ml.runtime.*` labels identify it as "Prime Agent" edition.
