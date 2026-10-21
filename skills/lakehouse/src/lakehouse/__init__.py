"""Read-only SQL over the Meridian Iceberg tables, returning pandas DataFrames.

The connection and the read-only guard are services.common.impala, the same
code the bridge and validation scripts use: one guard for the whole project.
Ranger is the real policy on CDW; the guard only stops a model-written write
from ever being sent.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[4]  # skills/lakehouse/src/lakehouse/__init__.py
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import pandas as pd  # noqa: E402

from services.common.env import settings  # noqa: E402
from services.common.impala import QueryResult, ReadOnlyViolation, run_query  # noqa: E402

__all__ = ["query", "tables", "describe", "profile", "partitions", "snapshots", "explain",
           "show", "evidence", "ReadOnlyViolation"]

# Capped printing: a stray `df` at the end of a cell must not flood the context.
pd.set_option("display.max_rows", 40)
pd.set_option("display.min_rows", 20)
pd.set_option("display.max_columns", 30)
pd.set_option("display.width", 200)
pd.set_option("display.max_colwidth", 60)

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PROFILE_MINMAX = ("tinyint", "smallint", "int", "bigint", "float", "double", "decimal", "date", "timestamp")
_history: list[dict] = []


def _frame(r: QueryResult) -> pd.DataFrame:
    df = pd.DataFrame.from_records(r.rows, columns=r.columns)
    df.attrs.update(sql=r.sql, row_count=r.row_count, truncated=r.truncated, elapsed_ms=r.elapsed_ms)
    _history.append({"sql": r.sql, "rows": r.row_count, "truncated": r.truncated, "elapsed_ms": r.elapsed_ms})
    return df


def _table(name: str) -> str:
    """`table` or `db.table`, identifiers only: these are interpolated into SQL."""
    parts = name.strip().strip("`").split(".")
    if len(parts) > 2 or not all(_IDENT.match(p) for p in parts):
        raise ValueError(f"not a table name: {name!r}")
    return ".".join(parts) if len(parts) == 2 else f"{settings().impala.database}.{parts[0]}"


def query(sql: str, max_rows: int | None = None) -> pd.DataFrame:
    """Run one read-only statement; return at most ``max_rows`` rows as a DataFrame.

    ``df.attrs``: ``sql``, ``row_count``, ``truncated``, ``elapsed_ms``.
    Raises ReadOnlyViolation for anything but SELECT/WITH/SHOW/DESCRIBE/EXPLAIN.
    """
    return _frame(run_query(sql, max_rows=max_rows))


def tables() -> pd.DataFrame:
    """Tables in the investigation database."""
    return query(f"SHOW TABLES IN {settings().impala.database}")


def describe(table: str) -> pd.DataFrame:
    """Columns of ``table``: name, type, comment."""
    return query(f"DESCRIBE {_table(table)}")


def profile(table: str) -> pd.DataFrame:
    """One row per column: non_null, distinct (approximate), min, max; ``attrs['rows']`` is the total."""
    name = _table(table)
    cols = describe(name)
    exprs = ["COUNT(*)"]
    for col, kind in zip(cols["name"], cols["type"]):
        minmax = kind.lower().startswith(_PROFILE_MINMAX)
        exprs += [f"COUNT(`{col}`)", f"NDV(`{col}`)",
                  f"CAST(MIN(`{col}`) AS STRING)" if minmax else "CAST(NULL AS STRING)",
                  f"CAST(MAX(`{col}`) AS STRING)" if minmax else "CAST(NULL AS STRING)"]
    row = query(f"SELECT {', '.join(exprs)} FROM {name}").iloc[0].tolist()
    total, rest = int(row[0]), row[1:]
    out = pd.DataFrame(
        [{"column": c, "type": t, "non_null": int(rest[4 * i]), "distinct": int(rest[4 * i + 1]),
          "min": rest[4 * i + 2], "max": rest[4 * i + 3]}
         for i, (c, t) in enumerate(zip(cols["name"], cols["type"]))])
    out.attrs.update(table=name, rows=total)
    return out


def partitions(table: str) -> pd.DataFrame:
    """The Iceberg ``partitions`` metadata table: one row per partition with record and file counts."""
    return query(f"SELECT * FROM {_table(table)}.`partitions`")


def snapshots(table: str) -> pd.DataFrame:
    """The Iceberg commit history of ``table``, oldest first."""
    return query(f"SELECT committed_at, snapshot_id, parent_id, operation FROM {_table(table)}.snapshots "
                 "ORDER BY committed_at")


def explain(sql: str) -> str:
    """Impala's plan for one read-only statement, without running it."""
    df = query(f"EXPLAIN {sql.strip().rstrip(';')}", max_rows=10_000)
    return "\n".join(str(v) for v in df.iloc[:, 0])


def show(df: pd.DataFrame, n: int = 20) -> None:
    """Print the first ``n`` rows and how many were left out."""
    print(df.head(n).to_string())
    if len(df) > n:
        print(f"... {len(df) - n} more rows")


def evidence() -> pd.DataFrame:
    """Every statement this kernel ran through the skill, in order."""
    return pd.DataFrame(_history, columns=["sql", "rows", "truncated", "elapsed_ms"])
