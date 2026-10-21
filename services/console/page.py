"""Investigation Console (BURNDOWN H1): investigations, the session tree, and
per-session SQL, rows, elapsed time and status. Reads through an
InvestigationSource, never from files directly."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from services.common.env import redacted, settings  # noqa: E402
from services.console.model import (  # noqa: E402
    ABANDONED, DONE, ENDED, RUNNING, STALLED, Investigation, InvestigationSource, Session,
)
from services.console.runs import FileRunSource  # noqa: E402

STATUS_ICON = {
    RUNNING: "🟡", DONE: "🟢", ENDED: "⚪", STALLED: "🔴",
    "completed": "🟢", "running": "🟡", ABANDONED: "🔴", "failed": "🔴",
}
STATUS_COLOR = {  # graphviz fill
    RUNNING: "#f6d36b", DONE: "#7fd38a", ENDED: "#d0d0d0", STALLED: "#f08a80",
    "completed": "#7fd38a", "running": "#f6d36b", ABANDONED: "#f08a80", "failed": "#f08a80",
}


@st.cache_resource
def source() -> InvestigationSource:
    s = settings()
    return FileRunSource(s.agent_workdir / "runs", s.agent_dir)


def fmt_elapsed(seconds: float | None) -> str:
    if seconds is None:
        return "–"
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}m {s:02d}s" if m else f"{s}s"


def fmt_int(n: int | None) -> str:
    return "–" if n is None else f"{n:,}"


def elapsed_of(item, live: bool) -> float | None:
    """Finished: start to end. Still going: start to now."""
    if item.elapsed_s is not None:
        return item.elapsed_s
    if live and item.started:
        return (datetime.now(timezone.utc) - item.started).total_seconds()
    return None


def tree_dot(root: Session) -> str:
    lines = ['digraph G {', 'rankdir=LR; bgcolor="transparent";',
             'node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=11];']

    def node(s: Session) -> None:
        label = f"{s.name}\\n{s.model}\\n{s.status} · {fmt_elapsed(s.elapsed_s)}"
        lines.append(f'"{s.id or s.name}" [label="{label}", fillcolor="{STATUS_COLOR.get(s.status, "#e8e8e8")}"];')
        for c in s.children:
            node(c)
            lines.append(f'"{s.id or s.name}" -> "{c.id}";')

    node(root)
    lines.append("}")
    return "\n".join(lines)


def session_table(inv: Investigation) -> pd.DataFrame:
    running = inv.summary.status == RUNNING
    rows = []
    for s in inv.root.walk():
        rows.append({
            "session": s.name if s.role == "root" else f"└ {s.name}",
            "model": s.model,
            "status": f"{STATUS_ICON.get(s.status, '')} {s.status}",
            "elapsed": fmt_elapsed(elapsed_of(s, running and s.status in (RUNNING, "running"))),
            "tokens": fmt_int(s.tokens),
            "cells": len(s.cells),
            "failed cells": sum(not c.ok for c in s.cells),
            "SQL in cells": sum(len(c.sql) for c in s.cells),
            "cited SQL": len(s.output.evidence) if s.output else 0,
            "cited rows": fmt_int(s.rows),
            "output": (s.output.name + (" ⚠ invalid JSON" if s.output.parse_error else "")) if s.output else "",
        })
    return pd.DataFrame(rows)


def show_session(s: Session) -> None:
    if s.role == "child":
        if s.output:
            st.markdown(f"**Output** `{s.output.name}`")
            if s.output.parse_error:
                st.warning(f"Not valid JSON: {s.output.parse_error}")
            if s.output.evidence:
                st.markdown("Cited evidence, as written by the child")
                for e in s.output.evidence:
                    st.caption(f"`{e.path}` · rows {fmt_int(e.row_count)} · "
                               f"{fmt_int(e.elapsed_ms)} ms" + (" · truncated" if e.truncated else ""))
                    st.code(e.sql, language="sql")
            else:
                st.caption("No SQL cited in the output file.")
            with st.popover("Raw output"):
                if s.output.data is not None:
                    st.json(s.output.data, expanded=False)
                else:
                    st.code(s.output.text)
        else:
            st.caption("No output file attributed to this child.")

    st.markdown(f"**Code cells** ({len(s.cells)}, {sum(not c.ok for c in s.cells)} failed)")
    for i, c in enumerate(s.cells, 1):
        mark = "✅" if c.ok else "❌"
        dur = f" · {c.duration_ms} ms" if c.duration_ms is not None else ""
        sql = f" · {len(c.sql)} SQL" if c.sql else ""
        with st.expander(f"{mark} cell {i}{dur}{sql}"):
            for q in c.sql:
                st.code(q, language="sql")
            st.markdown("Code")
            st.code(c.code, language="python")
            st.markdown("Result")
            st.code(c.result[:20_000] or "(no output)")


def show_investigation(inv: Investigation) -> None:
    sm = inv.summary
    running = sm.status == RUNNING
    st.subheader(sm.question or sm.run_id)
    st.caption(f"`{sm.run_id}` · root session `{inv.root.id}`")

    sessions = list(inv.root.walk())
    cols = st.columns(5)
    cols[0].metric("Status", f"{STATUS_ICON.get(sm.status, '')} {sm.status}")
    cols[1].metric("Elapsed", fmt_elapsed(elapsed_of(sm, running)))
    cols[2].metric("Children", sm.children)
    tokens = [s.tokens for s in sessions if s.tokens]
    cols[3].metric("Tokens", fmt_int(sum(tokens)) if tokens else "–")
    cols[4].metric("SQL in cells", sum(len(c.sql) for s in sessions for c in s.cells))

    st.graphviz_chart(tree_dot(inv.root), use_container_width=True)
    st.dataframe(session_table(inv), hide_index=True, use_container_width=True)

    tabs = st.tabs(["Findings", "Sessions", "Root reply", "Other outputs"])
    with tabs[0]:
        if inv.findings_md:
            st.markdown(inv.findings_md)
        else:
            st.info("No findings.md yet." if running else "This run wrote no findings.md.")
    with tabs[1]:
        by_id = {s.id or s.name: s for s in sessions}
        sid = st.selectbox(
            "Session", list(by_id), key=f"session-{sm.run_id}",
            format_func=lambda k: f"{STATUS_ICON.get(by_id[k].status, '')} {by_id[k].name} · {by_id[k].model}",
        )
        show_session(by_id[sid])
    with tabs[2]:
        st.markdown(inv.root_reply or "_No streamed text._")
    with tabs[3]:
        if not inv.unattributed:
            st.caption("Every output file is attributed to a child.")
        for o in inv.unattributed:
            with st.expander(o.name):
                if o.parse_error:
                    st.warning(f"Not valid JSON: {o.parse_error}")
                if o.data is not None:
                    st.json(o.data, expanded=False)
                else:
                    st.code(o.text)


def body() -> None:
    src = source()
    summaries = src.list_investigations()
    if not summaries:
        st.info(f"No investigations under `{settings().agent_workdir / 'runs'}` yet.")
        return

    left, right = st.columns([1, 3], gap="large")
    with left:
        st.markdown("**Investigations**")
        by_id = {s.run_id: s for s in summaries}
        picked = st.radio(
            "Investigations", list(by_id), key="run_id", label_visibility="collapsed",
            format_func=lambda rid: (
                f"{STATUS_ICON.get(by_id[rid].status, '')} {rid} · "
                f"{fmt_elapsed(elapsed_of(by_id[rid], by_id[rid].status == RUNNING))} · "
                f"{by_id[rid].children} children"
            ),
        )
        st.caption(f"Updated {datetime.now().strftime('%H:%M:%S')}")
    with right:
        inv = src.get_investigation(picked)
        if inv:
            show_investigation(inv)


st.set_page_config(page_title="Investigation Console", layout="wide")
st.title("Investigation Console")

with st.sidebar:
    auto = st.toggle("Auto-refresh", value=True)
    every = settings().console_refresh_s
    st.caption(f"Every {every} s from `{settings().agent_workdir / 'runs'}`")
    with st.expander("Environment"):
        st.json(redacted(settings()), expanded=False)

st.fragment(run_every=every if auto else None)(body)()
