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
    # Large 4 and Medium 3.5 need reasoning_effort, which pa-core v0.10.0 cannot send.
    assert m["mistral-large-4"]["reasoning"] is False
    assert m["mistral-medium-latest"]["reasoning"] is False
    assert m["magistral-medium-latest"]["reasoning"] is False
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


# ------------------------------------------------------- E5 investigation method
def test_harness_note_fits_the_digest():
    from render_config import DIGEST_CONTENT_LIMIT, HARNESS_NOTE
    assert len(HARNESS_NOTE) <= DIGEST_CONTENT_LIMIT and "AGENTS.md" in HARNESS_NOTE


def test_investigation_method_renders(monkeypatch):
    from render_config import investigation_note
    monkeypatch.setenv("MAX_CHILDREN", "4")
    settings.cache_clear()
    text = investigation_note(settings())
    settings.cache_clear()
    assert "$" not in text and "at most 4 children" in text and "meridian_financial" in text
    assert "SKILL.md" in text and "tool result" in text


def test_investigation_method_does_not_hint_at_the_answer():
    # The method must stay generic: the planted cause is the investigator's to find.
    text = (Path(__file__).resolve().parents[1] / "config/templates/investigation-note.md").read_text().lower()
    for hint in ("processor", "routing", "mastercard", "ecommerce", "4722", "4.12", "cross-border", "travel"):
        assert hint not in text, hint


def test_method_lists_exactly_the_skill_apis():
    # AGENTS.md carries the API so the model need not guess; it must not drift from the code.
    import re
    text = (Path(__file__).resolve().parents[1] / "config/templates/investigation-note.md").read_text()
    listed = set(re.findall(r"await (lakehouse|atlas)\.(\w+)\(", text))
    pytest.importorskip("pandas")
    repo = Path(__file__).resolve().parents[1]
    for mod in ("lakehouse", "atlas"):
        sys.path.insert(0, str(repo / "skills" / mod / "src"))
        module = __import__(mod)
        public = {n for n in module.__all__ if n[0].islower()}
        assert {n for m, n in listed if m == mod} == public, mod


# ------------------------------------------------------- E6 subagent spec
def test_partition_spec_renders_and_fits_the_digest(monkeypatch):
    from render_config import DIGEST_CONTENT_LIMIT, SUBAGENT_SPEC_ID, partition_investigator_spec
    monkeypatch.setenv("CHILD_MODEL", "mistral/ministral-14b-latest")
    settings.cache_clear()
    spec = partition_investigator_spec(settings())
    settings.cache_clear()
    first_line = spec.splitlines()[0]
    assert len(first_line) <= DIGEST_CONTENT_LIMIT and SUBAGENT_SPEC_ID in first_line
    assert 'model="mistral/ministral-14b-latest", thinking="off"' in spec and "$" not in spec


def test_partition_spec_does_not_hint_at_the_answer():
    text = (Path(__file__).resolve().parents[1] / "config/templates/partition-investigator.md").read_text().lower()
    for hint in ("processor", "routing", "mastercard", "ecommerce", "4722", "4.12", "cross-border", "travel"):
        assert hint not in text, hint


# ------------------------------------------------------- Option B task template
def test_task_template_renders_and_points_at_the_spec(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from run_investigation import render_task
    task = render_task("Why did X change?", "inv-1", tmp_path / "inv-1", 3, "mistral/ministral-14b-latest")
    assert "$" not in task and "Why did X change?" in task
    assert 'rlm.harness.get("subagent", "partition-investigator", global_=True)' in task
    assert 'model="mistral/ministral-14b-latest"' in task and "Use 3 subagents" in task
    assert "end your turn" in task and str(tmp_path / "inv-1") in task


def test_task_template_does_not_hint_at_the_answer():
    text = (Path(__file__).resolve().parents[1] / "config/templates/investigation-task.md").read_text().lower()
    for hint in ("processor", "routing", "mastercard", "ecommerce", "4722", "4.12", "cross-border", "travel"):
        assert hint not in text, hint
