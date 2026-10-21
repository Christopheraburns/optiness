#!/usr/bin/env python
"""D5: bankwide validation of the loaded dataset, SQL only, no agent.

Runs through the same read-only path the agent uses (services.common.impala,
default query options) and checks only what any analyst sees on day one: one
row per day, totals that match generation_runs, and a sustained bankwide lift
in the decline rate followed by recovery. The lift is found from the data;
nothing here names the incident window or its cause. Segment-level
reconciliation against the planted root cause lives in the Meridian repo
(scripts/reconcile_impala.py), never in this one: this repo is mounted where
the agent's Python runs.

Writes docs/data-validation.md and docs/daily-decline-rate.svg; exits 1 if any
check fails.
"""
from __future__ import annotations

import math
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from services.common.impala import run_query  # noqa: E402

DOCS = REPO / "docs"
BASELINE_DAYS = 14   # pooled reference: the first two weeks
LIFT_MIN_PP = 1.5    # a demo symptom must be obvious on a daily chart
RECOVERY_PP = 0.5    # the final week must be back near the reference
ELEVATED_PP = 1.0    # a day counts as elevated above reference + this

# Chart tokens (dataviz reference palette, light surface)
SURFACE, SERIES, INK, INK_2, MUTED, GRID = "#fcfcfb", "#2a78d6", "#0b0b0b", "#52514e", "#898781", "#e1e0d9"


def pct(declined: int, attempts: int) -> float:
    return 100 * declined / attempts if attempts else 0.0


def ci_pp(d1: int, n1: int, d0: int, n0: int) -> tuple[float, float, float]:
    """Difference of two decline rates in pp with an unpooled 95% interval."""
    p, q = d1 / n1, d0 / n0
    se = math.sqrt(p * (1 - p) / n1 + q * (1 - q) / n0)
    return 100 * (p - q), 100 * (p - q - 1.96 * se), 100 * (p - q + 1.96 * se)


def daily() -> list[tuple[date, int, int]]:
    r = run_query("""
        SELECT CAST(transaction_ts AS DATE) AS day, COUNT(*) AS attempts,
               SUM(CASE WHEN approved THEN 0 ELSE 1 END) AS declined
        FROM payment_transactions
        WHERE generation_run_id = (SELECT MAX(generation_run_id) FROM generation_runs WHERE run_status = 'complete')
        GROUP BY 1 ORDER BY 1""", max_rows=10_000)
    return [(d if isinstance(d, date) else date.fromisoformat(str(d)[:10]), int(n), int(k)) for d, n, k in r.rows]


def run_info() -> tuple[str, int, int]:
    r = run_query("""SELECT generation_run_id, generated_transaction_count, COUNT(*) OVER () AS runs
                     FROM generation_runs WHERE run_status = 'complete'""")
    run_id, count, runs = r.rows[0]
    return run_id, int(count), int(runs)


def chart(days: list[tuple[date, int, int]], reference: float) -> str:
    w, h, left, right, top, bottom = 760, 300, 56, 20, 44, 40
    rates = [pct(k, n) for _, n, k in days]
    hi = math.ceil(max(rates) + 0.5)
    x = lambda i: left + i * (w - left - right) / (len(days) - 1)  # noqa: E731
    y = lambda v: top + (hi - v) * (h - top - bottom) / hi  # noqa: E731
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" font-family="system-ui, sans-serif" font-size="12" role="img" aria-labelledby="t">',
           f'<title id="t">Daily card decline rate, all transactions</title>',
           f'<rect width="{w}" height="{h}" fill="{SURFACE}"/>',
           f'<text x="{left}" y="20" fill="{INK}" font-size="14" font-weight="600">Daily decline rate, all card authorizations (%)</text>']
    for v in range(0, hi + 1):
        out.append(f'<line x1="{left}" x2="{w - right}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="{GRID}" stroke-width="1"/>')
        out.append(f'<text x="{left - 8}" y="{y(v) + 4:.1f}" fill="{MUTED}" text-anchor="end">{v}</text>')
    for i, (d, _, _) in enumerate(days):
        if d.day in (1, 15):
            out.append(f'<text x="{x(i):.1f}" y="{h - bottom + 18}" fill="{MUTED}" text-anchor="middle">{d:%b %-d}</text>')
    out.append(f'<line x1="{left}" x2="{w - right}" y1="{y(reference):.1f}" y2="{y(reference):.1f}" stroke="{INK_2}" stroke-width="1" stroke-dasharray="4 4"/>')
    out.append(f'<text x="{w - right}" y="{y(reference) + 16:.1f}" fill="{INK_2}" text-anchor="end">first {BASELINE_DAYS} days: {reference:.2f}%</text>')
    points = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(rates))
    out.append(f'<polyline points="{points}" fill="none" stroke="{SERIES}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')
    out.append("</svg>")
    return "\n".join(out)


def main() -> int:
    run_id, generated, runs = run_info()
    days = daily()
    n_total, k_total = sum(n for _, n, _ in days), sum(k for _, _, k in days)
    span = (days[-1][0] - days[0][0]).days + 1

    ref_n = sum(n for _, n, _ in days[:BASELINE_DAYS]); ref_k = sum(k for _, _, k in days[:BASELINE_DAYS])
    reference = pct(ref_k, ref_n)
    weeks = [days[i:i + 7] for i in range(0, len(days), 7)]
    weekly = [(w[0][0], w[-1][0], sum(n for _, n, _ in w), sum(k for _, _, k in w)) for w in weeks]
    peak = max(weekly, key=lambda wk: pct(wk[3], wk[2]))
    peak_diff, peak_lo, _ = ci_pp(peak[3], peak[2], ref_k, ref_n)
    last_n = sum(n for _, n, _ in days[-7:]); last_k = sum(k for _, _, k in days[-7:])
    last_diff = pct(last_k, last_n) - reference
    elevated = [d for d, n, k in days if pct(k, n) > reference + ELEVATED_PP]

    checks = [
        ("one complete run loaded", runs == 1, f"{runs} complete run(s)"),
        ("transactions match generation_runs", n_total == generated, f"{n_total:,} vs {generated:,}"),
        ("one row per calendar day, no gaps", span == len(days), f"{len(days)} days over a {span}-day span"),
        (f"peak week lifts the decline rate by >= {LIFT_MIN_PP} pp", peak_diff >= LIFT_MIN_PP and peak_lo > 0,
         f"+{peak_diff:.2f} pp (95% CI lower {peak_lo:+.2f}), week of {peak[0]}"),
        (f"final week within {RECOVERY_PP} pp of the reference", abs(last_diff) <= RECOVERY_PP, f"{last_diff:+.2f} pp"),
    ]
    ok = all(passed for _, passed, _ in checks)

    DOCS.mkdir(exist_ok=True)
    (DOCS / "daily-decline-rate.svg").write_text(chart(days, reference))
    lines = [
        "# Data validation (bankwide)",
        "",
        "Generated by `make validate-data` (`scripts/validate_bankwide.py`) through the agent's",
        "read-only query path. Bankwide only: segment-level reconciliation with the planted",
        "root cause is done in the Meridian repo and is deliberately not recorded here.",
        "",
        f"Run `{run_id}`: {n_total:,} authorization attempts, {days[0][0]} to {days[-1][0]},",
        f"overall decline rate {pct(k_total, n_total):.2f}%. Reference (first {BASELINE_DAYS} days): {reference:.2f}%.",
        f"Days more than {ELEVATED_PP} pp above the reference: {len(elevated)}"
        + (f", {elevated[0]} to {elevated[-1]}." if elevated else "."),
        "",
        "![Daily decline rate](daily-decline-rate.svg)",
        "",
        "| Check | Result | Detail |",
        "|---|---|---|",
        *[f"| {name} | {'PASS' if passed else 'FAIL'} | {detail} |" for name, passed, detail in checks],
        "",
        "| Week | Attempts | Declined | Decline rate | vs reference |",
        "|---|---:|---:|---:|---:|",
        *[f"| {a} to {b} | {n:,} | {k:,} | {pct(k, n):.2f}% | {pct(k, n) - reference:+.2f} pp |" for a, b, n, k in weekly],
        "",
    ]
    (DOCS / "data-validation.md").write_text("\n".join(lines))
    for name, passed, detail in checks:
        print(f"[validate] {'PASS' if passed else 'FAIL'}  {name}: {detail}")
    print(f"[validate] {'PASS' if ok else 'FAIL'}; wrote docs/data-validation.md and docs/daily-decline-rate.svg")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
