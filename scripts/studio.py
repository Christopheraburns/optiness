#!/usr/bin/env python
"""Mistral Studio setup for the demo (BURNDOWN G1-G3), through the official `mistralai` SDK.

    make studio-check          # G1: the key works in the SDK and can reach models, connectors, agents

Runs on the host with an ephemeral environment (`uv run --with mistralai ...`), so the
runtime image does not need the SDK. MISTRAL_API_KEY comes from the environment or the
repo's .env. Connectors are a Mistral Public Preview API (`client.beta.connectors`).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def env(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name]
    f = REPO / ".env"
    if f.is_file():
        for line in f.read_text().splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    return ""


def client():
    try:
        from mistralai.client import Mistral  # SDK 3.x (mistralai is a namespace package)
    except ImportError:
        try:
            from mistralai import Mistral  # SDK 1.x
        except ImportError:
            raise SystemExit("mistralai is not installed: run through `make studio-check` (uv run --with mistralai)")
    key = env("MISTRAL_API_KEY")
    if not key:
        raise SystemExit("MISTRAL_API_KEY is not set (environment or .env)")
    return Mistral(api_key=key)


def check(_args) -> int:
    """G1: the key authenticates in the SDK and reaches the three APIs the demo uses."""
    from importlib.metadata import version
    c, failures = client(), []

    def step(what, fn):
        try:
            detail = fn()
            print(f"PASS {what}{f' ({detail})' if detail else ''}")
        except Exception as e:  # noqa: BLE001  report every failure, keep going
            failures.append(what)
            print(f"FAIL {what}: {type(e).__name__}: {str(e)[:300]}")

    print(f"mistralai {version('mistralai')}")
    root, child = env("ROOT_MODEL").split("/")[-1], env("CHILD_MODEL").split("/")[-1]

    def models():
        ids = {m.id for m in c.models.list().data}
        missing = [m for m in (root, child) if m and m not in ids]
        if missing:
            raise RuntimeError(f"models not visible to this key: {missing}")
        return f"{len(ids)} models incl. {root} and {child}"

    def connectors():
        page = c.beta.connectors.list(page_size=50)
        items = getattr(page, "items", None) or getattr(page, "data", None) or []
        custom = [x.name for x in items if getattr(x, "server", None)]
        return f"{len(items)} visible; custom MCP: {custom or 'none yet'}"

    def agents():
        lister = getattr(c.beta.agents, "list_pages", None)
        if lister is None:
            return f"{len(c.beta.agents.list())} agents in the workspace"
        result = lister().result
        items = getattr(result, "items", None) or getattr(result, "data", None) or result or []
        return f"{len(items)} agents on the first page"

    def chat():
        # reasoning_effort="none": Large 4 and Medium 3.5 reason by default on this API (the
        # capability pa-core v0.10.0 cannot switch on); a 5-token check should not think.
        r = c.chat.complete(model=root, max_tokens=50, reasoning_effort="none",
                            messages=[{"role": "user", "content": "Reply with OK."}])
        content = r.choices[0].message.content
        # Reasoning models answer with thinking chunks before the text; keep the text.
        if not isinstance(content, str):
            kinds = sorted({getattr(ch, "type", "?") for ch in content})
            content = "".join(getattr(ch, "text", "") for ch in content if getattr(ch, "type", "") == "text")
            return f"{root} replied {content.strip()!r} (chunks: {', '.join(kinds)})"
        return f"{root} replied {content.strip()!r}"

    step("models.list (root and child models available)", models)
    step("beta.connectors.list (connector scope)", connectors)
    step("beta.agents.list", agents)
    step("chat.complete on the root model", chat)
    print("\nG1 PASS" if not failures else f"\nG1 FAIL: {', '.join(failures)}")
    return 0 if not failures else 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="G1: verify the API key in the SDK").set_defaults(fn=check)
    a = p.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
