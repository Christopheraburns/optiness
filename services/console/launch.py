"""New investigation (BURNDOWN H9): the preset questions and the exact prompt the root agent receives.

The preview reuses the bridge's own code, so it cannot drift from what runs: the runner's
`task_question` (question plus scope) rendered through the engine's `render_task` template.
"""
from __future__ import annotations

import json
from pathlib import Path

from services.common.env import settings
from services.common.investigation import render_task

PRESETS = Path(__file__).resolve().parents[2] / "config" / "console-presets.json"
MIN_QUESTION = 5


def presets() -> list[dict]:
    try:
        return json.loads(PRESETS.read_text()).get("presets", [])
    except (OSError, json.JSONDecodeError):
        return []


def task_question(question: str, scope: str | None) -> str:
    from services.bridge.runner import Runner  # the bridge's own wording; only follow-ups use its store
    return Runner.task_question(None, {"question": question.strip(), "scope": {"text": scope} if scope else None})


def preview(question: str, scope: str | None, subagents: int) -> str:
    s = settings()
    child = f"{s.child_model.provider}/{s.child_model.model}"
    return render_task(task_question(question, scope), run_id="inv-<assigned when it starts>",
                       run_dir=Path("<run directory>"), children=subagents, child_model=child)


def validate(question: str, scope: str | None, subagents: object) -> tuple[str, str | None, int]:
    if not isinstance(question, str) or len(question.strip()) < MIN_QUESTION:
        raise ValueError("Write a question of at least a few words.")
    if scope is not None and not isinstance(scope, str):
        raise ValueError("Scope must be text.")
    if not isinstance(subagents, int) or isinstance(subagents, bool) or not 1 <= subagents <= settings().max_children:
        raise ValueError(f"Workers must be between 1 and {settings().max_children}.")
    return question.strip(), (scope or "").strip() or None, subagents
