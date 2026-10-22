"""Dataset (BURNDOWN H10): the lakehouse the agents query, read live through the read-only path.

The table list, one query counting every table, and per table its columns, format, partitioning,
snapshots and storage, plus descriptions and classifications from the metadata catalog the agents
also use. Relationships are inferred from the schema: a table's key is its first column, and
another table holding a column of that name references it; a column present in every table (run
lineage, such as generation_run_id) is not a relationship. Nothing here is engine-specific in what
it shows; reading table properties uses the warehouse's DESCRIBE FORMATTED.
"""
from __future__ import annotations

import threading
import time
from typing import Any

from services.common.impala import run_query
from services.common.metadata import backend

CACHE_S = 600
_lock = threading.Lock()
_cache: dict[str, Any] = {}


def _int(v: Any) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def formatted(rows: list[tuple]) -> dict[str, Any]:
    """Columns, partition transforms and table parameters from DESCRIBE FORMATTED rows."""
    columns, transforms, params, section = [], [], {}, "columns"
    for row in rows:
        first = (row[0] or "").strip()
        second = (row[1] or "").strip() if len(row) > 1 and row[1] is not None else ""
        third = (row[2] or "").strip() if len(row) > 2 and row[2] is not None else ""
        if first.startswith("# Partition"):
            section = "transforms"; continue
        if first.startswith("# Detailed Table"):
            section = "details"; continue
        if first.startswith("# Storage"):
            section = "storage"; continue
        if first.startswith("#") or (not first and not second):
            continue
        if section == "columns" and first:
            columns.append({"name": first, "type": second.lower()})
        elif section == "transforms" and first:
            transforms.append({"column": first, "transform": second.upper()})
        elif section == "details" and not first and second:
            params[second] = third
    table_type = params.get("table_type", "").upper()
    return {
        "columns": columns, "partitioning": transforms,
        "format": "Apache Iceberg" if table_type == "ICEBERG" else (params.get("table_type") or "Table"),
        "snapshots": _int(params.get("snapshot-count")), "files": _int(params.get("numFiles")),
        "bytes": _int(params.get("totalSize")),
    }


def relationships(tables: dict[str, dict]) -> list[dict]:
    keys = {name: t["columns"][0]["name"] for name, t in tables.items() if t["columns"]}
    everywhere = {k for k in keys.values() if all(k in {c["name"] for c in t["columns"]} for t in tables.values())}
    edges = []
    for name, t in tables.items():
        cols = {c["name"] for c in t["columns"]}
        for other, key in keys.items():
            if other != name and key in cols and key not in everywhere:
                edges.append({"from": name, "to": other, "key": key})
    return edges


def _catalog(names: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    try:
        meta = backend()
        for name in names:
            hit = next((e for e in meta.search(name, limit=20) if e.name == name), None)
            if hit:
                out[name] = {"description": hit.description, "owner": hit.owner, "classifications": hit.classifications}
    except Exception:  # noqa: BLE001 - the catalog is optional context; the warehouse facts stand alone
        pass
    return out


def build() -> dict[str, Any]:
    queries = []

    def q(sql: str, max_rows: int = 10_000):
        r = run_query(sql, max_rows=max_rows)
        queries.append({"sql": r.sql, "rows": r.row_count, "elapsed_ms": r.elapsed_ms})
        return r

    names = sorted(str(row[0]) for row in q("SHOW TABLES").rows)
    if not names:
        return {"tables": [], "relationships": [], "queries": queries}
    counts_sql = "\nUNION ALL\n".join(f"SELECT '{n}' AS table_name, COUNT(*) AS row_count FROM {n}" for n in names)
    counts = {str(t): int(c) for t, c in q(counts_sql).rows}
    tables: dict[str, dict] = {}
    for n in names:
        info = formatted(q(f"DESCRIBE FORMATTED {n}", max_rows=500).rows)
        if info["partitioning"]:
            info["partitions"] = q(f"SHOW PARTITIONS {n}", max_rows=100_000).row_count
        tables[n] = {"name": n, "rows": counts.get(n), **info}
    edges = relationships(tables)
    fact = max(names, key=lambda n: tables[n]["rows"] or 0)
    referenced = {e["to"] for e in edges if e["from"] == fact}
    catalog = _catalog(names)
    for n, t in tables.items():
        t["role"] = "fact" if n == fact else ("dimension" if n in referenced else "context")
        t.update(catalog.get(n, {"description": "", "owner": "", "classifications": []}))
    return {"tables": [tables[n] for n in names], "relationships": edges, "fact": fact, "queries": queries,
            "total_rows": sum(t["rows"] or 0 for t in tables.values()),
            "total_bytes": sum(t["bytes"] or 0 for t in tables.values()) or None}


def dataset(refresh: bool = False) -> dict[str, Any]:
    with _lock:
        if not refresh and _cache and time.time() - _cache["at"] < CACHE_S:
            return {**_cache["data"], "cached_s": round(time.time() - _cache["at"])}
        data = {**build(), "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        _cache.update(at=time.time(), data=data)
        return {**data, "cached_s": 0}
