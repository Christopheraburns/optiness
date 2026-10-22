"""Overview (BURNDOWN H5): bankwide payment health, measured live from the warehouse.

One aggregated query (daily attempts and declines over the whole fact table) through the
agent's read-only path, cached briefly. The degradation is detected from the data alone,
as an operations team would see it: the reference is the first two weeks, a day is
elevated when its decline rate sits clearly above the reference's own day-to-day spread,
and the anomaly is the longest run of elevated days. Nothing here knows the planted
cause; this view shows the symptom, the investigation finds the cause.
"""
from __future__ import annotations

import statistics
import threading
import time
from datetime import date
from typing import Any

from services.common.impala import run_query

# Engagement facts: the fact table and its outcome column. Customer builds change these.
DAILY_SQL = """SELECT CAST(transaction_ts AS DATE) AS day,
       COUNT(*) AS attempts,
       SUM(CASE WHEN approved THEN 0 ELSE 1 END) AS declines
FROM payment_transactions
GROUP BY 1
ORDER BY 1"""
REFERENCE_DAYS = 14   # pooled reference: the first two weeks
SPREAD_SIGMAS = 6     # elevated: above the reference by this many day-to-day standard deviations...
FLOOR_PP = 0.25       # ...and by at least this many percentage points
RECOVERY_DAYS = 7     # recovered when the last week is back inside the reference band
CACHE_S = 300

_lock = threading.Lock()
_cache: dict[str, Any] = {}


def _day(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def analyse(rows: list[tuple[date, int, int]]) -> dict[str, Any]:
    days = [{"day": d.isoformat(), "attempts": n, "declines": k,
             "decline_rate": 100 * k / n if n else 0.0, "success_rate": 100 * (n - k) / n if n else 0.0}
            for d, n, k in rows]
    attempts = sum(d["attempts"] for d in days)
    declines = sum(d["declines"] for d in days)
    out: dict[str, Any] = {"days": days, "total_attempts": attempts, "total_declines": declines,
                           "success_rate": 100 * (attempts - declines) / attempts if attempts else None,
                           "anomaly": None, "reference": None}
    if len(days) < REFERENCE_DAYS + 3:
        return out
    ref = days[:REFERENCE_DAYS]
    ref_rate = 100 * sum(d["declines"] for d in ref) / sum(d["attempts"] for d in ref)
    spread = statistics.pstdev(d["decline_rate"] for d in ref)
    threshold = ref_rate + max(SPREAD_SIGMAS * spread, FLOOR_PP)
    out["reference"] = {"days": REFERENCE_DAYS, "from": ref[0]["day"], "to": ref[-1]["day"],
                        "decline_rate": ref_rate, "success_rate": 100 - ref_rate,
                        "threshold_decline_rate": threshold}
    elevated = [d["decline_rate"] > threshold for d in days]
    best, start = (0, -1, -1), None
    for i, flag in enumerate(elevated + [False]):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            best = max(best, (i - start, start, i - 1))
            start = None
    length, first, last = best
    if not length:
        return out
    window = days[first:last + 1]
    w_attempts = sum(d["attempts"] for d in window)
    w_declines = sum(d["declines"] for d in window)
    expected = w_attempts * ref_rate / 100
    peak = max(window, key=lambda d: d["decline_rate"])
    tail = days[-RECOVERY_DAYS:]
    out["anomaly"] = {
        "start": window[0]["day"], "end": window[-1]["day"], "days": length,
        "attempts": w_attempts, "declines": w_declines,
        "decline_rate": 100 * w_declines / w_attempts, "success_rate": 100 * (w_attempts - w_declines) / w_attempts,
        "drop_pp": 100 * w_declines / w_attempts - ref_rate,
        "excess_declines": round(w_declines - expected),
        "peak": {"day": peak["day"], "success_rate": peak["success_rate"], "drop_pp": peak["decline_rate"] - ref_rate},
        "recovered": last < len(days) - RECOVERY_DAYS and all(d["decline_rate"] <= threshold for d in tail),
    }
    return out


def overview(refresh: bool = False) -> dict[str, Any]:
    """The analysed series plus the query that produced it; raises on warehouse errors."""
    with _lock:
        if not refresh and _cache and time.time() - _cache["at"] < CACHE_S:
            return {**_cache["data"], "cached_s": round(time.time() - _cache["at"])}
        result = run_query(DAILY_SQL, max_rows=10_000)
        rows = [(_day(d), int(n), int(k)) for d, n, k in result.rows]
        data = {**analyse(rows), "query": {"sql": result.sql, "rows": result.row_count, "elapsed_ms": result.elapsed_ms},
                "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        _cache.update(at=time.time(), data=data)
        return {**data, "cached_s": 0}
