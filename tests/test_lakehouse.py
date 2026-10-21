"""E3: the lakehouse skill against local Impala with the D4 dataset loaded.

Runs where the skill runs, in the agent kernel's Python: `make test-impala`
executes this file as a script inside the bridge (no pytest there). Under
pytest on a host without impyla or Impala, every test skips.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills" / "lakehouse" / "src"))

try:
    import pytest
except ImportError:  # script mode in the bridge
    pytest = None

if pytest is not None:
    pytest.importorskip("impala")
    pytest.importorskip("pandas")

import inspect  # noqa: E402

sys.path.insert(0, str(REPO / "tests"))
import lakehouse as _lakehouse  # noqa: E402
from sync_skill import Sync  # noqa: E402

lakehouse = Sync(_lakehouse)

if pytest is not None:
    try:
        lakehouse.query("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"local Impala not reachable: {exc}", allow_module_level=True)


def test_public_functions_are_async():
    # prime-agent teaches `await <skill>.fn()`; a sync skill costs the model turns.
    for name in _lakehouse.__all__:
        fn = getattr(_lakehouse, name)
        assert not callable(fn) or inspect.isclass(fn) or inspect.iscoroutinefunction(fn), name


def test_tables_and_describe():
    assert "payment_transactions" in set(lakehouse.tables()["name"])
    cols = lakehouse.describe("payment_transactions")
    assert len(cols) == 28 and {"transaction_ts", "routing_rule_id", "approved"} <= set(cols["name"])
    assert len(lakehouse.describe("meridian_financial.merchants")) == 14


def test_query_returns_dataframe_with_evidence():
    before = len(lakehouse.evidence())
    df = lakehouse.query("SELECT COUNT(*) AS n FROM payment_transactions")
    assert int(df.iloc[0, 0]) == 2_000_000
    assert df.attrs["row_count"] == 1 and df.attrs["truncated"] is False and df.attrs["elapsed_ms"] >= 0
    assert "payment_transactions" in df.attrs["sql"]
    assert len(lakehouse.evidence()) == before + 1


def test_row_cap_marks_truncation():
    df = lakehouse.query("SELECT transaction_id FROM payment_transactions", max_rows=5)
    assert len(df) == 5 and df.attrs["truncated"] is True


def test_profile():
    p = lakehouse.profile("payment_processors")
    assert p.attrs["rows"] == 8 and len(p) == 9
    row = p.set_index("column").loc["processor_id"]
    assert row["non_null"] == 8 and row["min"] == "0" and row["max"] == "7"


def test_iceberg_metadata():
    parts = lakehouse.partitions("payment_transactions")
    assert len(parts) == 75 and int(parts["record_count"].sum()) == 2_000_000
    # Demo hygiene: a clean load is appends only. Deletes or overwrites (a resumed or
    # hand-patched load) leave delete files and history an investigator would chase.
    assert int(parts["position_delete_record_count"].sum()) == 0
    assert set(lakehouse.snapshots("payment_transactions")["operation"]) == {"append"}


def test_explain_does_not_run():
    plan = lakehouse.explain("SELECT COUNT(*) FROM payment_transactions WHERE transaction_ts >= '2026-10-01'")
    assert "SCAN" in plan.upper()


def test_writes_and_injection_refused():
    for sql in ("DROP TABLE payment_transactions", "INSERT INTO t SELECT 1", "SELECT 1; DROP TABLE t",
                "EXPLAIN INSERT INTO t SELECT 1", "WITH x AS (SELECT 1) INSERT INTO t SELECT * FROM x"):
        try:
            lakehouse.query(sql)
        except lakehouse.ReadOnlyViolation:
            continue
        raise AssertionError(f"not refused: {sql}")
    for name in ("x; DROP TABLE y", "a.b.c", "payment_transactions`--"):
        try:
            lakehouse.profile(name)
        except ValueError:
            continue
        raise AssertionError(f"not refused: {name}")


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
