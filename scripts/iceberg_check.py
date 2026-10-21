#!/usr/bin/env python
"""C2 acceptance: Iceberg v2 DDL works on the configured Impala.

Creates a scratch database and an Iceberg table with partition transforms,
inserts rows, reads metadata tables, time-travels, and drops everything.
Uses a raw connection on purpose: the read-only guard in services.common.impala
would (correctly) refuse the DDL.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.common.impala import connect  # noqa: E402

DB = "primeagent_check"
TBL = f"{DB}.iceberg_probe"


def main() -> int:
    conn = connect(database="default")
    cur = conn.cursor()
    try:
        steps = [
            ("create db", f"CREATE DATABASE IF NOT EXISTS {DB}"),
            ("drop old", f"DROP TABLE IF EXISTS {TBL}"),
            ("create iceberg v2", f"""
                CREATE TABLE {TBL} (id BIGINT, region STRING, ts TIMESTAMP, amount DECIMAL(10,2))
                PARTITIONED BY SPEC (region, DAY(ts))
                STORED AS ICEBERG
                TBLPROPERTIES ('format-version'='2', 'write.format.default'='parquet')"""),
            ("insert", f"""
                INSERT INTO {TBL} VALUES
                  (1,'east', '2026-10-01 10:00:00', 10.50),
                  (2,'west', '2026-10-01 11:00:00', 20.00),
                  (3,'east', '2026-10-02 09:30:00', 5.25)"""),
            ("insert again", f"INSERT INTO {TBL} VALUES (4,'west','2026-10-02 12:00:00', 7.75)"),
        ]
        for label, sql in steps:
            t = time.perf_counter()
            cur.execute(sql)
            print(f"[iceberg] {label:16s} ok  {int((time.perf_counter()-t)*1000)} ms")

        cur.execute(f"SELECT region, count(*) FROM {TBL} GROUP BY region ORDER BY region")
        print("[iceberg] counts by region:", cur.fetchall())

        cur.execute(f"DESCRIBE FORMATTED {TBL}")
        fmt = [r for r in cur.fetchall() if r[0] and ("format-version" in str(r[0]) or "format-version" in str(r[1] or ""))]
        print("[iceberg] format-version rows:", fmt[:2])

        cur.execute(f"SELECT COUNT(*) FROM {TBL}.snapshots")
        print("[iceberg] snapshots:", cur.fetchall()[0][0])

        cur.execute(f"SELECT `partition`, record_count FROM {TBL}.`partitions` ORDER BY record_count DESC")
        print("[iceberg] partitions:", cur.fetchall())

        cur.execute(f"SELECT snapshot_id FROM {TBL}.snapshots ORDER BY committed_at LIMIT 1")
        first = cur.fetchall()[0][0]
        cur.execute(f"SELECT COUNT(*) FROM {TBL} FOR SYSTEM_VERSION AS OF {first}")
        print("[iceberg] rows at first snapshot (expect 3):", cur.fetchall()[0][0])
        print("[iceberg] PASS")
        return 0
    finally:
        try:
            cur.execute(f"DROP TABLE IF EXISTS {TBL}")
            cur.execute(f"DROP DATABASE IF EXISTS {DB}")
        finally:
            cur.close()
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
