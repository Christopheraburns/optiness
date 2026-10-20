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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.common.env import redacted, settings  # noqa: E402


def models_json(s) -> dict:
    """``{"providers": {name: ProviderConfig}}`` per pa-core models/custom.rs."""
    providers: dict[str, dict] = {}

    # Mistral: declare the root and child models explicitly so the run does not
    # depend on the remote model catalog being reachable from the container.
    mistral_models = {}
    for sel, reasoning, ctx, max_out in (
        (s.root_model, True, s.root_context_window, s.root_max_tokens),
        (s.child_model, False, s.child_context_window, s.child_max_tokens),
    ):
        if sel.provider == "mistral":
            mistral_models[sel.model] = {
                "id": sel.model,
                "name": sel.model,
                "reasoning": reasoning,
                "input": ["text"],
                "contextWindow": ctx,
                "maxTokens": max_out,
            }
    providers["mistral"] = {
        "name": "Mistral",
        "api": "mistral-conversations",
        "baseUrl": s.mistral_base_url,
        "apiKey": s.mistral_api_key or "unset",
        "models": list(mistral_models.values()),
    }

    # Cloudera AI Inference: an OpenAI-compatible endpoint serving a private model.
    if s.caii_enabled:
        providers["cloudera-ai-inference"] = {
            "name": "Cloudera AI Inference",
            "api": "openai-completions",
            "baseUrl": s.caii_base_url,
            "apiKey": s.caii_token or "unset",
            "models": [
                {
                    "id": s.caii_model,
                    "name": f"{s.caii_model} (Cloudera AI Inference)",
                    "reasoning": False,
                    "input": ["text"],
                    "contextWindow": s.caii_context_window,
                    "maxTokens": s.caii_max_tokens,
                }
            ],
        }
    return {"providers": providers}


def settings_json(s, existing: dict | None) -> dict:
    """Merge the keys this project owns into whatever settings already exist,
    so interactive tweaks made in the TUI (theme, keybindings) survive."""
    out = dict(existing or {})
    out.update(
        {
            "defaultProvider": s.root_model.provider,
            "defaultModel": s.root_model.model,
            "subagentDefaultModel": s.child_model.selector,
            "defaultThinkingLevel": s.root_thinking,
            "rlmMaxDepth": s.rlm_max_depth,
            "quietStartup": True,
            "telemetry": {"enabled": False},
            "compaction": {"enabled": True, "thresholdPercent": s.compaction_threshold_percent},
            "skills": [str(s.skills_dir)],
            "enableBuiltinSkills": True,
            "mcpServers": dict((existing or {}).get("mcpServers", {})),
        }
    )
    return out


def write_json(path: Path, payload: dict) -> bool:
    text = json.dumps(payload, indent=2) + "\n"
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

    changed_models = write_json(agent_dir / "models.json", models_json(s))
    changed_settings = write_json(settings_path, settings_json(s, existing))

    print(f"[render_config] agent_dir={agent_dir}")
    print(f"[render_config] models.json {'written' if changed_models else 'unchanged'}")
    print(f"[render_config] settings.json {'written' if changed_settings else 'unchanged'}")
    for k, v in redacted(s).items():
        print(f"[render_config]   {k}={v}")
    if not s.mistral_api_key:
        print("[render_config] WARNING: MISTRAL_API_KEY is empty; model calls will fail", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
