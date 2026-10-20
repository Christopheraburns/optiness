# syntax=docker/dockerfile:1.7
#
# Prime Agent runtime image for Cloudera AI Workbench and Docker Desktop.
#
# One image serves both targets. Workbench runs it as user cdsw with the
# project mounted over /home/cdsw, so nothing that must survive lives in
# /home/cdsw at build time. Prime Agent, its Python kernel venv, and uv are
# installed under /opt/prime-agent and are read-only at runtime.
#
# Build (from repo root):
#   docker build -t prime-agent-runtime:0.10.0 -f image/Dockerfile image
#   docker build --build-arg TARGETARCH=arm64 ... for Apple Silicon local runs
#
# Cloudera base image tags are listed on the Workbench "Start Session" page.

ARG CLOUDERA_RUNTIME=docker.repository.cloudera.com/cloudera/cdsw/ml-runtime-pbj-workbench-python3.11-standard:2025.09.1-b5
FROM ${CLOUDERA_RUNTIME}

ARG PRIME_AGENT_VERSION=0.10.0
# Set automatically by BuildKit: amd64 or arm64. Override only when cross-building.
ARG TARGETARCH
ARG UV_VERSION=0.13.4

LABEL org.opencontainers.image.title="Prime Agent on Cloudera AI" \
      org.opencontainers.image.description="Cloudera PBJ Workbench Python 3.11 runtime with Prime Agent ${PRIME_AGENT_VERSION}, a pre-warmed RLM kernel venv, impyla, and FastMCP" \
      org.opencontainers.image.source="https://github.com/PrimeIntellect-ai/prime-agent"

# Runtime metadata Cloudera reads when the image is registered in the Runtime Catalog.
ENV ML_RUNTIME_EDITION="Prime Agent" \
    ML_RUNTIME_SHORT_VERSION="2025.09" \
    ML_RUNTIME_MAINTENANCE_VERSION=1 \
    ML_RUNTIME_DESCRIPTION="PBJ Workbench Python 3.11 with Prime Agent ${PRIME_AGENT_VERSION} RLM harness, impyla, FastMCP"
ENV ML_RUNTIME_FULL_VERSION="${ML_RUNTIME_SHORT_VERSION}.${ML_RUNTIME_MAINTENANCE_VERSION}"
LABEL com.cloudera.ml.runtime.edition=$ML_RUNTIME_EDITION \
      com.cloudera.ml.runtime.full-version=$ML_RUNTIME_FULL_VERSION \
      com.cloudera.ml.runtime.short-version=$ML_RUNTIME_SHORT_VERSION \
      com.cloudera.ml.runtime.maintenance-version=$ML_RUNTIME_MAINTENANCE_VERSION \
      com.cloudera.ml.runtime.description=$ML_RUNTIME_DESCRIPTION

USER root
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# OS packages: tini for a clean PID 1 locally, SASL headers for impyla's
# thrift-sasl, curl and ca-certificates for the downloads below.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl tini git libsasl2-dev libsasl2-modules-gssapi-mit \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# uv: Prime Agent's kernel bootstrap looks for uv on PATH or ~/.local/bin/uv.
# Install it system-wide so the cdsw user finds it without a home directory.
# ---------------------------------------------------------------------------
ENV UV_INSTALL_DIR=/opt/prime-agent/bin \
    UV_NO_MODIFY_PATH=1 \
    UV_CACHE_DIR=/tmp/uv-cache
RUN mkdir -p /opt/prime-agent/bin \
 && curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" | sh \
 && /opt/prime-agent/bin/uv --version

# ---------------------------------------------------------------------------
# Prime Agent release payload. The binary resolves its runtime sidecar and
# bundled skills from the directory it lives in, so the whole tarball is kept
# together under /opt/prime-agent/pkg and the launcher is a symlink.
# ---------------------------------------------------------------------------
RUN set -eux; \
    case "${TARGETARCH:-amd64}" in \
      amd64) PLATFORM=linux-x64 ;; \
      arm64) PLATFORM=linux-arm64 ;; \
      *) echo "unsupported TARGETARCH ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    BASE="https://github.com/PrimeIntellect-ai/prime-agent/releases/download/v${PRIME_AGENT_VERSION}"; \
    ASSET="prime-agent-${PRIME_AGENT_VERSION}-${PLATFORM}.tar.gz"; \
    mkdir -p /opt/prime-agent/pkg /tmp/pa; \
    curl -fsSL -o "/tmp/pa/${ASSET}" "${BASE}/${ASSET}"; \
    curl -fsSL -o /tmp/pa/SHA256SUMS "${BASE}/SHA256SUMS"; \
    (cd /tmp/pa && grep "  ${ASSET}\$" SHA256SUMS | sha256sum -c -); \
    tar -xzf "/tmp/pa/${ASSET}" -C /opt/prime-agent/pkg; \
    rm -rf /tmp/pa; \
    ln -sf /opt/prime-agent/pkg/prime-agent /opt/prime-agent/bin/prime-agent; \
    /opt/prime-agent/bin/prime-agent --version

# ---------------------------------------------------------------------------
# Pre-warmed kernel venv. This mirrors what Prime Agent would do on first run
# (uv venv --python 3.11 --seed, then install the runtime sidecar, every
# bundled Python skill, and the default extras). Pointing
# PRIME_AGENT_KERNEL_PYTHON at it makes the binary validate and use this venv
# instead of bootstrapping one under $HOME, which is what keeps Workbench
# containers free of egress and home-directory writes at startup.
#
# Demo-specific packages (impyla, fastmcp, streamlit, ...) are installed into
# the same venv so the lakehouse skill and the bridge share one interpreter.
# ---------------------------------------------------------------------------
ENV PRIME_AGENT_KERNEL_VENV=/opt/prime-agent/kernel-venv \
    PRIME_AGENT_KERNEL_PYTHON=/opt/prime-agent/kernel-venv/bin/python \
    PRIME_AGENT_TELEMETRY=0 \
    DO_NOT_TRACK=1
COPY requirements-demo.txt /opt/prime-agent/requirements-demo.txt
RUN set -eux; \
    UV=/opt/prime-agent/bin/uv; \
    PKG=/opt/prime-agent/pkg; \
    VENV=/opt/prime-agent/kernel-venv; \
    "$UV" venv --python 3.11 --seed "$VENV"; \
    "$UV" pip install --python "$VENV/bin/python" \
        "$PKG/prime-agent-runtime" \
        requests httpx pyyaml tomli python-dotenv pandas numpy scipy \
        beautifulsoup4 lxml pydantic tyro dill; \
    for skill in "$PKG"/skills/*/; do \
        if [ -f "$skill/pyproject.toml" ]; then \
            "$UV" pip install --python "$VENV/bin/python" "$skill"; \
        fi; \
    done; \
    "$UV" pip install --python "$VENV/bin/python" -r /opt/prime-agent/requirements-demo.txt; \
    rm -rf /tmp/uv-cache; \
    "$VENV/bin/python" -c "import rlm, rlm.mcp, rlm.repl; from rlm.harness import HarnessEntry; print('rlm ok', rlm.repl.PROTOCOL_VERSION)"; \
    "$VENV/bin/python" -c "import impala.dbapi, pandas, fastmcp, mcp; print('demo deps ok')"

# ---------------------------------------------------------------------------
# Runtime layout. Workbench sets HOME=/home/cdsw and mounts the project there;
# Prime Agent keeps settings and sessions under $HOME/.prime/agent, which is
# therefore persistent in Workbench and a named volume locally. The daemon
# socket lives under $TMPDIR, which must be writable and short.
# ---------------------------------------------------------------------------
COPY --chmod=0755 entrypoint.sh /opt/prime-agent/bin/entrypoint.sh
COPY --chmod=0755 healthcheck.sh /opt/prime-agent/bin/healthcheck.sh
RUN chown -R root:root /opt/prime-agent \
 && find /opt/prime-agent -type d -exec chmod 0755 {} + \
 && chmod -R a+rX /opt/prime-agent \
 && mkdir -p /home/cdsw && chown 8536:8536 /home/cdsw

ENV PATH="/opt/prime-agent/bin:${PATH}" \
    TMPDIR=/tmp \
    PYTHONUNBUFFERED=1 \
    PRIME_AGENT_VERSION=${PRIME_AGENT_VERSION}

# cdsw is the user Workbench runs sessions and applications as (uid 8536).
# Running as the same uid locally keeps file ownership identical on the
# /home/cdsw volume. Workbench ignores USER and ENTRYPOINT and launches the
# application script itself, so both are safe to set here for local use.
USER 8536
WORKDIR /home/cdsw
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/prime-agent/bin/entrypoint.sh"]
CMD ["prime-agent", "--version"]
