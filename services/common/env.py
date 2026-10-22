"""The environment contract. One reader, typed, with the same defaults the
.env.example documents. Services import ``settings()`` and never call
``os.environ`` directly, so moving between Docker Desktop and Cloudera AI
Workbench is a matter of values, never code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except ValueError:
        return default


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class ModelSelector:
    """``provider/model`` as Prime Agent's spawn and settings expect it."""

    provider: str
    model: str

    @classmethod
    def parse(cls, raw: str, *, default: str) -> "ModelSelector":
        raw = (raw or default).strip()
        provider, _, model = raw.partition("/")
        if not model:
            raise ValueError(f"model selector must be provider/model, got {raw!r}")
        return cls(provider=provider, model=model)

    @property
    def selector(self) -> str:
        return f"{self.provider}/{self.model}"


@dataclass(frozen=True)
class ImpalaSettings:
    host: str
    port: int
    user: str
    password: str
    database: str
    auth_mechanism: str
    use_http: bool
    http_path: str
    use_ssl: bool
    query_timeout_s: int
    max_rows: int


@dataclass(frozen=True)
class AtlasSettings:
    backend: str  # stub | atlas
    url: str
    user: str
    password: str
    stub_path: Path


@dataclass(frozen=True)
class Settings:
    deploy_target: str
    bind_host: str
    app_port: int

    agent_dir: Path
    root_model: ModelSelector
    root_thinking: str
    root_context_window: int
    root_max_tokens: int
    child_model: ModelSelector
    child_thinking: str
    child_context_window: int
    child_max_tokens: int
    rlm_max_depth: int
    max_children: int
    compaction_threshold_tokens: int
    impala_mcp_url: str
    skills_dir: Path
    agent_workdir: Path

    mistral_api_key: str
    mistral_base_url: str


    impala: ImpalaSettings
    atlas: AtlasSettings

    bridge_bearer_token: str
    bridge_data_dir: Path
    investigation_timeout_s: int
    bridge_public_url: str
    bridge_internal_url: str
    console_refresh_s: int

    data_scale: int
    data_seed: int
    data_staging_dir: Path

    extra: dict[str, str] = field(default_factory=dict)



@lru_cache(maxsize=1)
def settings() -> Settings:
    """Read the contract once per process."""
    home = Path(os.environ.get("HOME", "/home/cdsw"))
    workbench = "CDSW_PROJECT" in os.environ or "CDSW_ENGINE_ID" in os.environ
    default_bind = "127.0.0.1" if workbench else "0.0.0.0"
    return Settings(
        deploy_target=_str("DEPLOY_TARGET", "cai" if workbench else "local"),
        bind_host=_str("BIND_HOST", default_bind),
        app_port=_int(os.environ.get("CDSW_APP_PORT"), 8080),
        agent_dir=Path(_str("PRIME_AGENT_CODING_AGENT_DIR", str(home / ".prime" / "agent"))),
        root_model=ModelSelector.parse(_str("ROOT_MODEL"), default="mistral/mistral-large-latest"),
        root_thinking=_str("ROOT_THINKING", "medium"),
        root_context_window=_int(os.environ.get("ROOT_CONTEXT_WINDOW"), 262_144),
        root_max_tokens=_int(os.environ.get("ROOT_MAX_TOKENS"), 16_384),
        child_model=ModelSelector.parse(_str("CHILD_MODEL"), default="mistral/ministral-8b-latest"),
        child_thinking=_str("CHILD_THINKING", "low"),
        child_context_window=_int(os.environ.get("CHILD_CONTEXT_WINDOW"), 262_144),
        child_max_tokens=_int(os.environ.get("CHILD_MAX_TOKENS"), 16_384),
        rlm_max_depth=_int(os.environ.get("RLM_MAX_DEPTH"), 2),
        max_children=_int(os.environ.get("MAX_CHILDREN"), 6),
        compaction_threshold_tokens=_int(os.environ.get("COMPACTION_THRESHOLD_TOKENS"), 96_000),
        impala_mcp_url=_str("IMPALA_MCP_URL").rstrip("/"),
        skills_dir=Path(_str("SKILLS_DIR", str(home / "skills"))),
        agent_workdir=Path(_str("AGENT_WORKDIR", str(home / "data" / "agent"))),
        mistral_api_key=_str("MISTRAL_API_KEY"),
        mistral_base_url=_str("MISTRAL_BASE_URL", "https://api.mistral.ai"),
        impala=ImpalaSettings(
            host=_str("IMPALA_HOST", "impalad"),
            port=_int(os.environ.get("IMPALA_PORT"), 21050),
            user=_str("IMPALA_USER"),
            password=_str("IMPALA_PASSWORD"),
            database=_str("IMPALA_DATABASE", "meridian_financial"),
            auth_mechanism=_str("IMPALA_AUTH_MECHANISM", "NOSASL").upper(),
            use_http=_bool(os.environ.get("IMPALA_USE_HTTP"), False),
            http_path=_str("IMPALA_HTTP_PATH", "cliservice"),
            use_ssl=_bool(os.environ.get("IMPALA_USE_SSL"), False),
            query_timeout_s=_int(os.environ.get("IMPALA_QUERY_TIMEOUT_S"), 600),
            max_rows=_int(os.environ.get("IMPALA_MAX_ROWS"), 5_000),
        ),
        atlas=AtlasSettings(
            backend=_str("METADATA_BACKEND", "stub").lower(),
            url=_str("ATLAS_URL").rstrip("/"),
            user=_str("ATLAS_USER"),
            password=_str("ATLAS_PASSWORD"),
            stub_path=Path(_str("ATLAS_STUB_PATH", str(home / "config" / "atlas-stub.json"))),
        ),
        bridge_bearer_token=_str("BRIDGE_BEARER_TOKEN"),
        bridge_data_dir=Path(_str("BRIDGE_DATA_DIR", str(home / "data" / "bridge"))),
        investigation_timeout_s=_int(os.environ.get("INVESTIGATION_TIMEOUT_S"), 3600),
        bridge_public_url=_str("BRIDGE_PUBLIC_URL").rstrip("/"),
        bridge_internal_url=_str("BRIDGE_INTERNAL_URL").rstrip("/"),
        console_refresh_s=_int(os.environ.get("CONSOLE_REFRESH_S"), 5),
        data_scale=_int(os.environ.get("DATA_SCALE"), 2_000_000),
        data_seed=_int(os.environ.get("DATA_SEED"), 20261027),
        data_staging_dir=Path(_str("DATA_STAGING_DIR", str(home / "data" / "staging"))),
    )


def redacted(s: Settings) -> dict[str, object]:
    """A printable view with secrets masked, for startup logs and smoke tests."""
    def mask(v: str) -> str:
        return "" if not v else f"{v[:3]}…({len(v)} chars)"

    return {
        "deploy_target": s.deploy_target,
        "bind": f"{s.bind_host}:{s.app_port}",
        "agent_dir": str(s.agent_dir),
        "root_model": s.root_model.selector,
        "child_model": s.child_model.selector,
        "rlm_max_depth": s.rlm_max_depth,
        "mistral_api_key": mask(s.mistral_api_key),
        "impala": f"{s.impala.host}:{s.impala.port}/{s.impala.database} auth={s.impala.auth_mechanism} http={s.impala.use_http} ssl={s.impala.use_ssl}",
        "impala_user": s.impala.user,
        "metadata_backend": s.atlas.backend,
        "bridge_bearer_token": mask(s.bridge_bearer_token),
        "bridge_data_dir": str(s.bridge_data_dir),
        "data_scale": s.data_scale,
    }
