#!/usr/bin/env python
"""D4: load a staged Meridian run into the meridian_financial Iceberg tables.

Runs in the bridge container (make data) or a Workbench session. For each table
it creates an external Parquet table in the `staging` database over the staged
directory, then INSERT ... SELECT into the existing Iceberg table, so every row
lands through a real Iceberg commit. Dimensions load first, payment_transactions
next, and generation_runs last: its row is the "this run is complete" marker.

Re-running is safe and resumes: each dimension table and each week of
transactions is skipped when already loaded and cleared when partial, so a run
that is already complete is only verified. Rows from a different run are
refused unless --replace, which truncates all ten tables.
The tables themselves are never created, dropped or altered (make schema owns them).

Uses a raw connection: the read-only guard in services.common.impala (correctly)
refuses writes.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.common.env import settings  # noqa: E402
from services.common.impala import connect  # noqa: E402

TARGET = "meridian_financial"
STAGING = "staging"
DIMENSIONS = (
    "payment_processors", "transaction_response_codes", "customers", "merchants", "payment_terminals",
    "routing_rules", "deployment_events", "processor_incidents",
)
FACT = "payment_transactions"
MARKER = "generation_runs"
TABLES = (*DIMENSIONS, FACT, MARKER)


class Loader:
    def __init__(self, cur, staging_uri: str, run_id: str):
        self.cur = cur
        self.run_id = run_id
        self.staging_uri = staging_uri.rstrip("/")

    def sql(self, statement: str, label: str | None = None):
        t = time.perf_counter()
        self.cur.execute(statement)
        rows = self.cur.fetchall() if self.cur.description else []
        if label:
            print(f"[load] {label:42s} {int((time.perf_counter() - t) * 1000):>7} ms")
        return rows

    def scalar(self, statement: str):
        return self.sql(statement)[0][0]

    def columns(self, table: str) -> list[tuple[str, str]]:
        return [(r[0], r[1]) for r in self.sql(f"DESCRIBE {TARGET}.{table}")]

    def stage(self, table: str) -> None:
        # Declare the staging columns from the target's own schema, so a Parquet
        # file that drifts from the contract fails here instead of loading.
        cols = ",\n  ".join(f"`{name}` {kind}" for name, kind in self.columns(table))
        self.sql(f"DROP TABLE IF EXISTS {STAGING}.{table}")
        self.sql(f"CREATE EXTERNAL TABLE {STAGING}.{table} (\n  {cols}\n) STORED AS PARQUET "
                 f"LOCATION '{self.staging_uri}/{table}'", f"stage {table}")

    def count(self, db: str, table: str, run_id: str | None = None, where: str = "TRUE") -> int:
        if run_id:
            where = f"generation_run_id = '{run_id}' AND ({where})"
        return int(self.scalar(f"SELECT COUNT(*) FROM {db}.{table} WHERE {where}"))

    def unit(self, table: str, insert: str, where: str, label: str, attempts: int = 3) -> None:
        """Load the staged rows matching ``where`` unless they are already loaded."""
        want = self.count(STAGING, table, where=where)
        if self.count(TARGET, table, self.run_id, where) == want:
            print(f"[load] {'skip ' + label:42s} {want:>10,} rows already loaded")
            return
        for attempt in range(1, attempts + 1):
            got = self.count(TARGET, table, self.run_id, where)
            if got == want:
                return
            if got:
                self.sql(f"DELETE FROM {TARGET}.{table} WHERE generation_run_id = '{self.run_id}' AND ({where})",
                         f"clear partial {label}")
            try:
                self.sql(insert, f"insert {label}")
            except Exception as e:  # noqa: BLE001 - a time-limited hang surfaces as a query error
                if attempt == attempts:
                    raise SystemExit(f"[load] {label} failed {attempts} times ({e}). If impalad is wedged, "
                                     "restart it (docker restart optiness-impalad-1) and re-run make data; "
                                     "loading resumes where it stopped")
                print(f"[load] {label} attempt {attempt} failed: {str(e)[:120]}; retrying")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--staging-dir", type=Path, default=settings().data_staging_dir / "meridian",
                   help="Staged run as this process sees it (data/stage.py output)")
    p.add_argument("--staging-uri", default=None,
                   help="The same directory as Impala sees it; default file://<staging-dir>. On CDW, the s3a:// or abfs:// prefix")
    p.add_argument("--replace", action="store_true", help="Truncate all ten tables if another run is loaded")
    a = p.parse_args()

    # Docker Desktop file sharing can lag a moment behind a fresh data/stage.py on the host.
    manifest_path = a.staging_dir / "public_manifest.json"
    for _ in range(30):
        if manifest_path.is_file():
            break
        time.sleep(1)
    else:
        raise SystemExit(f"{manifest_path} not found; run data/stage.py first (make data does)")
    manifest = json.loads(manifest_path.read_text())
    if not manifest.get("complete"):
        raise SystemExit("staged run is incomplete; re-run data/stage.py on a complete Meridian run")
    run_id, expected_rows = manifest["generation_run_id"], int(manifest["rows_generated"])
    uri = a.staging_uri or f"file://{a.staging_dir.resolve()}"
    print(f"[load] run {run_id}: {expected_rows:,} rows from {uri}")

    conn = connect(database="default")
    cur = conn.cursor()
    load = Loader(cur, uri, run_id)
    try:
        load.sql("SET PARQUET_FALLBACK_SCHEMA_RESOLUTION=NAME")
        if settings().deploy_target == "local":
            # The amd64 quickstart impalad under Rosetta wedges on multithreaded
            # partitioned Iceberg writes (every thrift server dies with EINTR) and
            # intermittently hangs in LLVM-codegen'd fragments, which Impala only
            # uses above 50k rows. Write single-threaded and interpreted locally;
            # the time limit turns a hang into a retry.
            for option in ("MT_DOP=0", "DISABLE_CODEGEN=true", "EXEC_TIME_LIMIT_S=180"):
                load.sql(f"SET {option}")
        load.sql(f"CREATE DATABASE IF NOT EXISTS {STAGING} COMMENT 'Transient external tables for data/load.py'")

        others = {t: load.count(TARGET, t) - load.count(TARGET, t, run_id) for t in TABLES}
        if any(others.values()):
            if not a.replace:
                raise SystemExit("meridian_financial holds another run "
                                 f"({others[FACT]:,} transactions); pass --replace (make data REPLACE=1)")
            for t in TABLES:
                load.sql(f"TRUNCATE TABLE {TARGET}.{t}", f"truncate {t}")

        for t in TABLES:
            load.stage(t)
        staged_run, = load.sql(f"SELECT generation_run_id, generated_transaction_count, run_status FROM {STAGING}.{MARKER}")
        if staged_run[0] != run_id or int(staged_run[1]) != expected_rows or staged_run[2] != "complete":
            raise SystemExit(f"staged generation_runs {staged_run} does not match the manifest")

        # Resumable: each unit (a dimension table, one week of transactions, then
        # the generation_runs marker) is skipped when already complete and
        # cleared when partial, so a re-run after a failure continues from there.
        for t in TABLES:
            names = ", ".join(f"`{n}`" for n, _ in load.columns(t))
            insert = f"INSERT INTO {TARGET}.{t} ({names}) SELECT {names} FROM {STAGING}.{t}"
            if t != FACT:
                load.unit(t, insert, "TRUE", t)
                continue
            first, last = load.sql(f"SELECT MIN(transaction_ts), MAX(transaction_ts) FROM {STAGING}.{FACT}")[0]
            start = datetime.combine(first.date(), datetime.min.time())
            while start <= last:
                end = start + timedelta(days=7)
                window = f"transaction_ts >= '{start}' AND transaction_ts < '{end}'"
                load.unit(t, f"{insert} WHERE {window}", window, f"{t} {start:%Y-%m-%d}..")
                start = end

        # Verify against the staged files, which are the generator's own output.
        failures = []
        for t in TABLES:
            want, got = load.count(STAGING, t), load.count(TARGET, t, run_id)
            print(f"[load] {t:30s} staged {want:>10,}  loaded {got:>10,}")
            if want != got:
                failures.append(t)
        loaded = load.count(TARGET, FACT, run_id)
        if loaded != expected_rows:
            failures.append(f"{FACT} has {loaded:,} rows, manifest says {expected_rows:,}")
        if failures:
            raise SystemExit(f"[load] FAIL: {failures}")

        for t in TABLES:
            load.sql(f"COMPUTE STATS {TARGET}.{t}")
        days = load.scalar(f"SELECT COUNT(*) FROM {TARGET}.{FACT}.`partitions`")
        print(f"[load] PASS: {loaded:,} transactions in {days} daily partitions; stats computed")
        return 0
    finally:
        try:
            for t in TABLES:
                cur.execute(f"DROP TABLE IF EXISTS {STAGING}.{t}")  # external: staged files stay
            cur.close()
            conn.close()
        except Exception as e:  # noqa: BLE001 - never mask the error that got us here
            print(f"[load] cleanup skipped: {type(e).__name__}: {e}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
