"""E1: models.json declares the Mistral set; Mistral is the only provider."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from render_config import MISTRAL_MODELS, models_json  # noqa: E402
from services.common.env import settings  # noqa: E402


@pytest.fixture
def env(monkeypatch):
    def make(**values):
        for k in ("ROOT_MODEL", "CHILD_MODEL", "ROOT_CONTEXT_WINDOW"):
            monkeypatch.delenv(k, raising=False)
        for k, v in values.items():
            monkeypatch.setenv(k, v)
        settings.cache_clear()
        return models_json(settings())
    yield make
    settings.cache_clear()


def models(doc, provider):
    return {m["id"]: m for m in doc["providers"][provider]["models"]}


def test_mistral_set_with_correct_reasoning_flags(env):
    m = models(env(MISTRAL_API_KEY="k"), "mistral")
    assert set(m) == {mid for mid, *_ in MISTRAL_MODELS}
    assert m["mistral-large-latest"]["reasoning"] is False
    assert m["mistral-large-4"]["reasoning"] is True
    assert m["magistral-medium-latest"]["reasoning"] is True
    assert not m["ministral-8b-latest"]["reasoning"]


def test_root_limits_capped_by_model(env):
    m = models(env(ROOT_MODEL="mistral/ministral-3b-latest", ROOT_CONTEXT_WINDOW="262144"), "mistral")
    assert m["ministral-3b-latest"]["contextWindow"] == 131_072


def test_unlisted_root_is_declared(env):
    m = models(env(ROOT_MODEL="mistral/mistral-large-2411"), "mistral")
    assert m["mistral-large-2411"]["reasoning"] is False


def test_mistral_is_the_only_provider(env):
    # A leftover CAII_* variable from an old .env must not bring a provider back.
    assert list(env(CAII_BASE_URL="http://ollama:11434/v1")["providers"]) == ["mistral"]


# ---------------------------------------------------------------- E2 settings
from render_config import settings_json, validate  # noqa: E402


@pytest.fixture
def rendered(monkeypatch):
    def make(existing=None, **values):
        for k in ("ROOT_MODEL", "CHILD_MODEL", "COMPACTION_THRESHOLD_TOKENS", "IMPALA_MCP_URL"):
            monkeypatch.delenv(k, raising=False)
        for k, v in values.items():
            monkeypatch.setenv(k, v)
        settings.cache_clear()
        return settings_json(settings(), existing)
    yield make
    settings.cache_clear()


def test_rendered_settings_are_all_known_and_typed(rendered):
    assert validate(rendered()) == []


def test_validate_catches_what_prime_agent_ignores():
    problems = validate({"compaction": {"enabled": True, "thresholdPercent": 85}, "rlmMaxDepth": "two", "bogus": 1})
    assert any("compaction.thresholdPercent" in p for p in problems)
    assert any("rlmMaxDepth should be int" in p for p in problems)
    assert any("'bogus'" in p for p in problems)


def test_compaction_fires_at_threshold_on_smallest_window(rendered):
    # pa-core threshold: min(0.95 * window, window - maxOutput - reserveTokens)
    doc = rendered(CHILD_MODEL="mistral/ministral-3b-latest", COMPACTION_THRESHOLD_TOKENS="96000")
    reserve = doc["compaction"]["reserveTokens"]
    assert 131_072 - 16_384 - reserve == 96_000          # ministral-3b compacts at the target
    assert 262_144 - 16_384 - reserve > 96_000           # the larger root compacts later, never disabled


def test_mistral_only_allowlist_and_retry(rendered):
    doc = rendered()
    assert doc["allowedModels"] == ["mistral/*"]
    assert doc["retry"]["maxRetries"] == 5


def test_mcp_optional_and_existing_entries_kept(rendered):
    keep = {"mcpServers": {"other": {"type": "stdio", "command": "x"}}, "theme": "dark",
            "compaction": {"thresholdPercent": 85}}
    doc = rendered(keep)
    assert doc["mcpServers"] == keep["mcpServers"] and doc["theme"] == "dark"
    assert "thresholdPercent" not in doc["compaction"]          # stale key replaced
    doc = rendered(keep, IMPALA_MCP_URL="https://mcp.example/mcp/")
    assert doc["mcpServers"]["impala"] == {"type": "http", "url": "https://mcp.example/mcp",
                                          "bearerTokenEnvVar": "IMPALA_MCP_TOKEN"}


def test_unimportable_skill_is_reported(tmp_path, monkeypatch):
    from render_config import unimportable_skills
    (tmp_path / "lakehouse" / "src").mkdir(parents=True)
    (tmp_path / "lakehouse" / "pyproject.toml").write_text("")
    (tmp_path / "notes").mkdir()                       # markdown-only skill: nothing to import
    monkeypatch.setenv("PYTHONPATH", "")
    assert len(unimportable_skills(tmp_path)) == 1
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "lakehouse" / "src"))
    assert unimportable_skills(tmp_path) == []
