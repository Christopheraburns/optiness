"""Impala connection factory and the read-only guard.

The same code reaches the local Impala quickstart (binary transport, NOSASL)
and a Cloudera Data Warehouse virtual warehouse (HTTP transport, LDAP, SSL)
purely through the IMPALA_* variables. Ranger enforces policy on the server;
this module only refuses to send statements that are not reads.
"""
from __future__ import annotations

import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from .env import ImpalaSettings, settings

_READ_ONLY_PREFIXES = ("SELECT", "WITH", "SHOW", "DESCRIBE", "DESC", "EXPLAIN")
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|CREATE|ALTER|TRUNCATE|GRANT|REVOKE|REFRESH|INVALIDATE|LOAD|SET)\b",
    re.IGNORECASE,
)


class ReadOnlyViolation(ValueError):
    """Raised before anything is sent to Impala."""


def assert_read_only(sql: str) -> str:
    """Return the trimmed statement or raise. Comments are stripped first so a
    leading ``-- select`` cannot disguise a write."""
    body = re.sub(r"--[^\n]*", "", sql)
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL).strip().rstrip(";").strip()
    if not body:
        raise ReadOnlyViolation("empty statement")
    if ";" in body:
        raise ReadOnlyViolation("one statement per call")
    if not body.upper().startswith(_READ_ONLY_PREFIXES):
        raise ReadOnlyViolation(f"only read statements are allowed, got: {body[:40]!r}")
    # A CTE body may legitimately contain none of these; a SELECT never needs them.
    if _FORBIDDEN.search(body):
        raise ReadOnlyViolation("statement contains a write or DDL keyword")
    return body


def connect(cfg: ImpalaSettings | None = None, *, database: str | None = None):
    """Open an impyla DB-API connection from the env contract.

    ``database`` overrides IMPALA_DATABASE for callers that must work before
    the demo database exists (smoke tests, the loader)."""
    from impala.dbapi import connect as _connect  # imported lazily: skills load fast

    cfg = cfg or settings().impala
    kwargs: dict[str, Any] = {
        "host": cfg.host,
        "port": cfg.port,
        "database": database or cfg.database,
        "auth_mechanism": cfg.auth_mechanism,
        "use_ssl": cfg.use_ssl,
        "timeout": cfg.query_timeout_s,
    }
    if cfg.auth_mechanism in {"LDAP", "PLAIN"}:
        kwargs["user"] = cfg.user
        kwargs["password"] = cfg.password
    elif cfg.user:
        kwargs["user"] = cfg.user
    if cfg.use_http:
        kwargs["use_http_transport"] = True
        kwargs["http_path"] = cfg.http_path
    return _connect(**kwargs)


@dataclass
class QueryResult:
    sql: str
    columns: list[str]
    rows: list[tuple]
    row_count: int
    truncated: bool
    elapsed_ms: int

    def to_dataframe(self):
        import pandas as pd

        return pd.DataFrame.from_records(self.rows, columns=self.columns)


@contextmanager
def cursor(cfg: ImpalaSettings | None = None, *, database: str | None = None) -> Iterator[Any]:
    conn = connect(cfg, database=database)
    try:
        cur = conn.cursor()
        try:
            yield cur
        finally:
            cur.close()
    finally:
        conn.close()


def run_query(
    sql: str, *, max_rows: int | None = None, cfg: ImpalaSettings | None = None, database: str | None = None
) -> QueryResult:
    """Execute one read-only statement and fetch at most ``max_rows`` rows."""
    cfg = cfg or settings().impala
    body = assert_read_only(sql)
    limit = max_rows if max_rows is not None else cfg.max_rows
    started = time.perf_counter()
    with cursor(cfg, database=database) as cur:
        cur.execute(body)
        columns = [d[0] for d in (cur.description or [])]
        rows = cur.fetchmany(limit + 1) if columns else []
    truncated = len(rows) > limit
    rows = rows[:limit]
    return QueryResult(
        sql=body,
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )
