"""Dataset (H10): table properties from DESCRIBE FORMATTED rows, and relationships inferred from the schema."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.console.dataset import formatted, relationships  # noqa: E402

FACT_ROWS = [
    ("# col_name", "data_type", "comment"), ("", None, None),
    ("order_id", "bigint", None), ("order_ts", "timestamp", None), ("shop_id", "bigint", None),
    ("buyer_id", "bigint", None), ("run_id", "string", None), ("", None, None),
    ("# Partition Transform Information", None, None), ("# col_name", "transform_type", None),
    ("order_ts", "DAY", None), ("", None, None),
    ("# Detailed Table Information", None, None), ("Database:", "shop", None), ("Table Parameters:", None, None),
    ("", "numFiles            ", "12                  "), ("", "snapshot-count", "4"), ("", "table_type", "ICEBERG"),
    ("", "totalSize", "2048"), ("# Storage Information", None, None), ("SerDe Library:", "x", None),
]


def table(*cols: str) -> dict:
    return {"columns": [{"name": c, "type": "bigint"} for c in cols]}


def test_formatted_reads_columns_partitioning_and_properties():
    t = formatted(FACT_ROWS)
    assert [c["name"] for c in t["columns"]] == ["order_id", "order_ts", "shop_id", "buyer_id", "run_id"]
    assert t["partitioning"] == [{"column": "order_ts", "transform": "DAY"}]
    assert (t["format"], t["snapshots"], t["files"], t["bytes"]) == ("Apache Iceberg", 4, 12, 2048)
    plain = formatted([("a", "int", None), ("# Detailed Table Information", None, None), ("", "table_type", "MANAGED_TABLE")])
    assert plain["partitioning"] == [] and plain["format"] == "MANAGED_TABLE" and plain["snapshots"] is None


def test_relationships_follow_first_column_keys_and_skip_lineage_columns():
    tables = {
        "orders": table("order_id", "shop_id", "buyer_id", "run_id"),
        "shops": table("shop_id", "region_id", "run_id"),
        "buyers": table("buyer_id", "run_id"),
        "regions": table("region_id", "run_id"),
        "runs": table("run_id"),
    }
    edges = {(e["from"], e["to"], e["key"]) for e in relationships(tables)}
    assert edges == {("orders", "shops", "shop_id"), ("orders", "buyers", "buyer_id"), ("shops", "regions", "region_id")}


def test_route_reports_unavailable_without_a_warehouse(monkeypatch):
    pytest.importorskip("starlette"); pytest.importorskip("httpx")
    from starlette.testclient import TestClient
    from services.common import env
    from services.console.runs import FileRunSource
    from services.console.server import create_app
    fix = ROOT / "tests" / "fixtures" / "console"
    env.settings.cache_clear()
    monkeypatch.setenv("IMPALA_HOST", "")
    try:
        body = TestClient(create_app(FileRunSource(fix / "runs", fix / "prime-agent"))).get("/api/dataset").json()
    finally:
        env.settings.cache_clear()
    assert body["available"] is False and "IMPALA_HOST" in body["reason"]
