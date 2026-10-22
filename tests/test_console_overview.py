"""Overview (H5): anomaly detection from a daily series, on synthetic data only."""
from __future__ import annotations

import random
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.console.overview import analyse  # noqa: E402


def series(rates: list[float], volume: int = 500_000, seed: int = 7) -> list[tuple[date, int, int]]:
    rng = random.Random(seed)
    start = date(2026, 1, 1)
    out = []
    for i, rate in enumerate(rates):
        n = volume - (volume // 3 if (start + timedelta(i)).weekday() >= 5 else 0)
        k = round(n * (rate + rng.gauss(0, 0.0003)))
        out.append((start + timedelta(i), n, k))
    return out


def test_detects_a_sustained_rise_and_its_recovery():
    rates = [0.035] * 30 + [0.041] * 3 + [0.058] * 20 + [0.035] * 15
    r = analyse(series(rates))
    a = r["anomaly"]
    assert a is not None and a["start"] == "2026-01-31" and a["end"] == "2026-02-22" and a["days"] == 23
    assert a["recovered"] and 2.0 < a["drop_pp"] < 2.3 and a["excess_declines"] > 0
    assert abs(r["reference"]["decline_rate"] - 3.5) < 0.05
    assert r["total_attempts"] == sum(n for _, n, _ in series(rates))


def test_flat_series_has_no_anomaly_and_ongoing_one_is_not_recovered():
    assert analyse(series([0.035] * 60))["anomaly"] is None
    ongoing = analyse(series([0.035] * 40 + [0.06] * 10))["anomaly"]
    assert ongoing and not ongoing["recovered"]


def test_too_short_to_judge():
    r = analyse(series([0.035] * 10))
    assert r["anomaly"] is None and r["reference"] is None and r["success_rate"] == pytest.approx(96.5, abs=0.1)


def test_route_reports_unavailable_warehouse(monkeypatch):
    pytest.importorskip("starlette"); pytest.importorskip("httpx")
    from starlette.testclient import TestClient
    from services.common import env
    from services.console import overview
    from services.console.runs import FileRunSource
    from services.console.server import create_app
    fix = ROOT / "tests" / "fixtures" / "console"
    client = TestClient(create_app(FileRunSource(fix / "runs", fix / "prime-agent")))
    env.settings.cache_clear()
    monkeypatch.setenv("IMPALA_HOST", "")
    try:
        assert client.get("/api/overview").json() == {"available": False, "reason": "No warehouse is configured for the console (IMPALA_HOST)."}
        monkeypatch.setenv("IMPALA_HOST", "warehouse.example")
        env.settings.cache_clear()
        monkeypatch.setattr(overview, "overview", lambda refresh=False: (_ for _ in ()).throw(RuntimeError("connection refused")))
        body = client.get("/api/overview").json()
        assert body["available"] is False and "connection refused" in body["reason"]
    finally:
        env.settings.cache_clear()
