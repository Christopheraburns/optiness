#!/usr/bin/env python
"""Render Prime Agent's models.json and settings.json from the env contract.

Runs at container start (image entrypoint) and from ``make render``. It is
idempotent and writes only the two files Prime Agent reads from the agent dir,
leaving sessions and harness state untouched.

Why render instead of committing the files: Prime Agent has no env-variable
substitution in models.json, and the API keys and endpoints differ per
target. Rendering keeps secrets out of the repo and the file shapes in one
place.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.common.env import redacted, settings  # noqa: E402


# Mistral models the demo can select, as GET /v1/models reported them on
# 2026-10-21: (id, display name, context window, reasoning). All support tool
# calls. Declared explicitly so runs never depend on the remote catalog, and
# because the bundled catalog is stale for some (magistral-medium shows 128K).
# Only reasoning models may receive a thinking level: mistral-large-latest
# (= 2512) is not one. Large 4 and Medium 3.5 reason on the API, but only through
# `reasoning_effort` (high|none); pa-core v0.10.0's Mistral adapter sends the older
# `prompt_mode: reasoning`, which they reject (400, checked 2026-10-21), and its
# OpenAI-compatible adapter gets 422 from Mistral. So they are declared
# non-reasoning here and always run with thinking off.
MISTRAL_MODELS = (
    ("mistral-large-latest", "Mistral Large 3", 262_144, False),
    ("mistral-large-4", "Mistral Large 4", 1_048_576, False),
    ("mistral-medium-latest", "Mistral Medium 3.5", 262_144, False),
    ("magistral-medium-latest", "Magistral Medium (= Medium 3.5)", 262_144, False),
    ("magistral-small-latest", "Magistral Small", 262_144, True),
    ("ministral-14b-latest", "Ministral 14B", 262_144, False),
    ("ministral-8b-latest", "Ministral 8B", 262_144, False),
    ("ministral-3b-latest", "Ministral 3B", 131_072, False),
)
DEFAULT_MAX_TOKENS = 16_384


def models_json(s) -> dict:
    """``{"providers": {name: ProviderConfig}}`` per pa-core models/custom.rs."""
    providers: dict[str, dict] = {}

    mistral_models = {
        mid: {"id": mid, "name": name, "reasoning": reasoning, "input": ["text"],
              "contextWindow": ctx, "maxTokens": DEFAULT_MAX_TOKENS}
        for mid, name, ctx, reasoning in MISTRAL_MODELS
    }
    # The root and child selections take their limits from the env contract,
    # capped at what the model supports. An unlisted id is declared as given.
    for sel, ctx, max_out in (
        (s.root_model, s.root_context_window, s.root_max_tokens),
        (s.child_model, s.child_context_window, s.child_max_tokens),
    ):
        if sel.provider != "mistral":
            continue
        entry = mistral_models.setdefault(sel.model, {
            "id": sel.model, "name": sel.model, "reasoning": False, "input": ["text"],
            "contextWindow": ctx, "maxTokens": max_out})
        entry["contextWindow"] = min(entry["contextWindow"], ctx)
        entry["maxTokens"] = max_out
    providers["mistral"] = {
        "name": "Mistral",
        "api": "mistral-conversations",
        "baseUrl": s.mistral_base_url,
        "apiKey": s.mistral_api_key or "unset",
        "models": list(mistral_models.values()),
    }

    return {"providers": providers}


# Top-level keys of pa-core's Settings at v0.10.0 (crates/pa-core/src/settings/types.rs).
# The loader is lenient: an unknown key is kept but ignored and a wrong-typed value
# loads as unset, both silently, so validate() is the only thing that catches them.
SETTINGS_KEYS = frozenset({
    "agentTraces", "allowedModels", "autoRefine", "autocompleteMaxVisible", "autonomous",
    "auxiliaryModel", "branchSummary", "bundledSkills", "chatDetail", "compaction", "defaultModel",
    "defaultProvider", "defaultServiceTier", "defaultThinkingLevel", "editorPaddingX",
    "enableBuiltinSkills", "enableSkillCommands", "enabledModels", "factory", "followUpMode",
    "idleEvictionMinutes", "imageModel", "images", "markdown", "mcpCatalogSources", "mcpServers",
    "npmCommand", "onboardingCompleted", "onboardingShown", "packages", "prompts",
    "providerBackupModel", "quietStartup", "recentModels", "retry", "rlmMaxDepth",
    "sessionArchiveMaxAgeDays", "sessionArchiveMaxSessions", "sessionDir", "shellCommandPrefix",
    "shellPath", "showHardwareCursor", "skills", "steeringMode", "subagentDefaultModel", "telemetry",
    "terminal", "theme", "themes", "thinkingBudgets", "transport", "treeFilterMode", "updateChannel",
    "warnings",
})
# Types of the keys this project writes, nested objects as dicts of their own keys.
OWNED_TYPES = {
    "defaultProvider": str, "defaultModel": str, "subagentDefaultModel": str,
    "defaultThinkingLevel": str, "rlmMaxDepth": int, "quietStartup": bool,
    "telemetry": {"enabled": bool}, "skills": list, "enableBuiltinSkills": bool,
    "allowedModels": list, "mcpServers": dict,
    "compaction": {"enabled": bool, "reserveTokens": int, "keepRecentTokens": int, "agentCallable": bool},
    "retry": {"enabled": bool, "maxRetries": int, "baseDelayMs": int},
}
# Provider retries: v0.10.0 defaults to 3 retries from 2 s (about 14 s in all).
# Five covers a full one-minute rate-limit window; Retry-After is honored up to 60 s.
RETRY = {"enabled": True, "maxRetries": 5, "baseDelayMs": 2000}
COMBINED_LIMIT_HEADROOM_FLOOR = 4_096  # pa-core session_engine/compaction.rs


def compaction_reserve(s, models: dict) -> int:
    """reserveTokens that makes compaction fire at COMPACTION_THRESHOLD_TOKENS.

    pa-core compacts at min(0.95 * window, window - maxOutput - reserveTokens), and
    reserveTokens is global. Solve on the smallest root/child window so no model's
    threshold goes non-positive (that silently disables compaction); models with
    larger windows compact somewhat later.
    """
    sizes = [(m["contextWindow"], m["maxTokens"]) for sel in (s.root_model, s.child_model)
             if sel.provider == "mistral" and (m := models.get(sel.model))]
    window, max_out = min(sizes) if sizes else (s.root_context_window, s.root_max_tokens)
    return max(COMBINED_LIMIT_HEADROOM_FLOOR, window - max_out - s.compaction_threshold_tokens)


def settings_json(s, existing: dict | None) -> dict:
    """Merge the keys this project owns into whatever settings already exist,
    so interactive tweaks made in the TUI (theme, keybindings) survive."""
    out = dict(existing or {})
    models = {m["id"]: m for m in models_json(s)["providers"]["mistral"]["models"]}
    mcp = dict(out.get("mcpServers", {}))
    if s.impala_mcp_url:
        mcp["impala"] = {"type": "http", "url": s.impala_mcp_url, "bearerTokenEnvVar": "IMPALA_MCP_TOKEN"}
    out.update(
        {
            "defaultProvider": s.root_model.provider,
            "defaultModel": s.root_model.model,
            "subagentDefaultModel": s.child_model.selector,
            "defaultThinkingLevel": s.root_thinking,
            "rlmMaxDepth": s.rlm_max_depth,
            "quietStartup": True,
            "telemetry": {"enabled": False},
            "compaction": {"enabled": True, "reserveTokens": compaction_reserve(s, models)},
            "retry": dict(RETRY),
            # Every model call goes to Mistral; anything else fails loudly in the daemon.
            "allowedModels": ["mistral/*"],
            "skills": [str(s.skills_dir)],
            "enableBuiltinSkills": True,
            "mcpServers": mcp,
        }
    )
    return out


def validate(doc: dict) -> list[str]:
    """Problems prime-agent would silently ignore: unknown keys, wrong types."""
    problems = [f"unknown setting {k!r} (ignored by prime-agent)" for k in doc if k not in SETTINGS_KEYS]

    def check(path: str, value, expected) -> None:
        if isinstance(expected, dict):
            if not isinstance(value, dict):
                problems.append(f"{path} should be an object")
                return
            for k, v in value.items():
                if k not in expected:
                    problems.append(f"unknown setting {path}.{k} (ignored by prime-agent)")
                else:
                    check(f"{path}.{k}", v, expected[k])
        elif not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
            problems.append(f"{path} should be {expected.__name__}, got {type(value).__name__} (loads as unset)")

    for key, expected in OWNED_TYPES.items():
        if key in doc:
            check(key, doc[key], expected)
    return problems


TEMPLATES = Path(__file__).resolve().parents[1] / "config" / "templates"
INVESTIGATION_NOTE_ID = "investigation-method"
# The harness digest shows each entry as a summary of at most 140 chars (pa-core
# refinement DEFAULT_OVERVIEW_CONTENT_LIMIT, v0.10.0), so the full method goes to
# AGENTS.md, which prime-agent puts in the system prompt whole. The harness entry
# is the short form that survives the digest.
DIGEST_CONTENT_LIMIT = 140
HARNESS_NOTE = ("Investigations: follow the AGENTS.md method; read each skill's SKILL.md first; "
                "every number from a tool result; cite SQL and row counts.")


def investigation_note(s) -> str:
    """The full investigation method (AGENTS.md), rendered from its template."""
    from string import Template

    return Template((TEMPLATES / "investigation-note.md").read_text()).substitute(
        database=s.impala.database, max_children=s.max_children, rlm_max_depth=s.rlm_max_depth).strip()


SUBAGENT_SPEC_ID = "partition-investigator"


def partition_investigator_spec(s) -> str:
    """The partition-investigator subagent spec (E6), rendered from its template.

    Its first line is the digest summary (<= 140 chars) and points at the full text.
    """
    from string import Template

    return Template((TEMPLATES / "partition-investigator.md").read_text()).substitute(
        database=s.impala.database, max_children=s.max_children, child_model=s.child_model.selector).strip()


def managed_harness_entries(s) -> list[tuple[str, str, str, str]]:
    """(kind, id, title, content) of every global harness entry this project owns."""
    return [
        ("prompt", INVESTIGATION_NOTE_ID, "Investigation method", HARNESS_NOTE),
        ("subagent", SUBAGENT_SPEC_ID, "Partition investigator", partition_investigator_spec(s)),
    ]


def seed_harness(s) -> str:
    """Upsert this project's entries into the global harness state (E5, E6).

    Uses prime-agent's own harness API, so the file format and locking stay
    theirs; needs the kernel Python (the image entrypoint runs this script with
    it). Agent-made entries are left alone; only the managed ids are touched.
    """
    try:
        from rlm.harness import get_harness_state
    except ImportError:
        return "skipped (rlm runtime not importable; run with PRIME_AGENT_KERNEL_PYTHON)"
    state = get_harness_state(global_=True)
    results = []
    for kind, entry_id, title, content in managed_harness_entries(s):
        meta = {"source": "config/templates", "managedBy": "scripts/render_config.py"}
        current = state.get(kind, entry_id, global_=True)
        if current is None:
            state.create(kind, title, content, id=entry_id, path="policy" if kind == "prompt" else "general",
                         metadata=meta, global_=True)
            results.append(f"{entry_id} created")
        elif current.content != content or current.title != title:
            state.update(kind, entry_id, title, content, metadata=meta, global_=True)
            results.append(f"{entry_id} updated")
        else:
            results.append(f"{entry_id} unchanged")
    return ", ".join(results)


def unimportable_skills(skills_dir: Path) -> list[str]:
    """Executable skills (a pyproject.toml beside SKILL.md) whose src dir is not on
    PYTHONPATH. prime-agent disables those with one easily missed warning, and a
    model then tends to invent the skill's output instead of reporting the error."""
    on_path = {str(Path(p).resolve()) for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p}
    return [f"skill {d.name!r} is not importable by the agent kernel; add {d / 'src'} to PYTHONPATH"
            for d in sorted(skills_dir.glob("*")) if (d / "pyproject.toml").is_file()
            and str((d / "src").resolve()) not in on_path]


def write_json(path: Path, payload: dict) -> bool:
    return write_text(path, json.dumps(payload, indent=2) + "\n")


def write_text(path: Path, text: str) -> bool:
    if path.exists() and path.read_text() == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return True


def main() -> int:
    s = settings()
    agent_dir = s.agent_dir
    agent_dir.mkdir(parents=True, exist_ok=True)

    settings_path = agent_dir / "settings.json"
    existing = None
    if settings_path.exists():
        try:
            existing = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            existing = None

    rendered = settings_json(s, existing)
    changed_models = write_json(agent_dir / "models.json", models_json(s))
    changed_settings = write_json(settings_path, rendered)
    for problem in validate(rendered):
        print(f"[render_config] WARNING: settings.json: {problem}", file=sys.stderr)

    print(f"[render_config] agent_dir={agent_dir}")
    print(f"[render_config] models.json {'written' if changed_models else 'unchanged'}")
    print(f"[render_config] settings.json {'written' if changed_settings else 'unchanged'}")
    s.agent_workdir.mkdir(parents=True, exist_ok=True)
    changed_agents = write_text(s.agent_workdir / "AGENTS.md", investigation_note(s) + "\n")
    print(f"[render_config] {s.agent_workdir / 'AGENTS.md'} {'written' if changed_agents else 'unchanged'}")
    print(f"[render_config] harness: {seed_harness(s)}")
    for k, v in redacted(s).items():
        print(f"[render_config]   {k}={v}")
    for problem in unimportable_skills(s.skills_dir):
        print(f"[render_config] WARNING: {problem}", file=sys.stderr)
    if not s.mistral_api_key:
        print("[render_config] WARNING: MISTRAL_API_KEY is empty; model calls will fail", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
