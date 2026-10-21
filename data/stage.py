#!/usr/bin/env python
"""D4, host side: stage one complete Meridian public run for the Impala loader.

Meridian writes payment_transactions/*.parquet plus all nine dimension files in
one dimensions/ directory. Impala external tables point at directories, so this
lays the run out one directory per table under data/staging/meridian/, which
compose mounts read-only into the Impala containers at the same path the bridge
sees (DATA_STAGING_DIR). Files are hard-linked when possible, so staging a 50M
run costs no extra disk.

Only public output is accepted. Meridian's private tree (ground truth, batch
audit) is refused: the agent's Python runs where this directory is mounted.
Standard library only, so it runs on the host without the runtime image.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PRIVATE_MARKERS = ("scenario_ground_truth.parquet", "generation_batch_audit.parquet", "scenario_quality.json")


def link_or_copy(src: Path, dst: Path) -> None:
    try:
        os.link(src, dst)
    except OSError:  # different filesystem
        shutil.copy2(src, dst)


def stage(source: Path, dest: Path) -> dict:
    manifest_path = source / "public_manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"{source} is not a Meridian public run (no public_manifest.json)")
    if any(next(source.rglob(m), None) for m in PRIVATE_MARKERS):
        raise SystemExit(f"{source} contains Meridian private output; stage only the public tree")
    manifest = json.loads(manifest_path.read_text())
    if not manifest.get("complete"):
        raise SystemExit(f"{source} is incomplete (public_manifest.json complete=false); not staging it")

    facts = sorted((source / "payment_transactions").glob("*.parquet"))
    dims = sorted((source / "dimensions").glob("*.parquet"))
    if not facts or not dims:
        raise SystemExit(f"{source} has no payment_transactions or dimensions Parquet files")

    if dest.exists():
        shutil.rmtree(dest)
    for f in facts:
        (dest / "payment_transactions").mkdir(parents=True, exist_ok=True)
        link_or_copy(f, dest / "payment_transactions" / f.name)
    for f in dims:
        (dest / f.stem).mkdir(parents=True, exist_ok=True)
        link_or_copy(f, dest / f.stem / f.name)
    shutil.copy2(manifest_path, dest / "public_manifest.json")
    return {"run_id": manifest["generation_run_id"], "rows": manifest["rows_generated"],
            "fact_files": len(facts), "dimension_tables": len(dims)}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--source", required=True, type=Path, help="Meridian public run, e.g. ../meridian/data/demo-public")
    p.add_argument("--dest", type=Path, default=REPO / "data" / "staging" / "meridian")
    a = p.parse_args()
    info = stage(a.source.resolve(), a.dest.resolve())
    print(f"[stage] run {info['run_id']}: {info['rows']:,} rows in {info['fact_files']} files, "
          f"{info['dimension_tables']} dimension tables -> {a.dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
