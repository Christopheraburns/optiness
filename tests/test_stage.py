"""D4: data/stage.py lays out one directory per table and refuses unsafe input."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data"))

from stage import stage  # noqa: E402


def fake_run(root: Path, complete: bool = True) -> Path:
    (root / "payment_transactions").mkdir(parents=True)
    (root / "dimensions").mkdir()
    for i in range(2):
        (root / "payment_transactions" / f"part-00000{i}.parquet").write_bytes(b"PAR1")
    for name in ("customers", "generation_runs"):
        (root / "dimensions" / f"{name}.parquet").write_bytes(b"PAR1")
    (root / "public_manifest.json").write_text(json.dumps(
        {"generation_run_id": "run-1", "rows_generated": 10, "complete": complete}))
    return root


def test_one_directory_per_table(tmp_path):
    info = stage(fake_run(tmp_path / "public"), tmp_path / "out")
    assert info == {"run_id": "run-1", "rows": 10, "fact_files": 2, "dimension_tables": 2}
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == [
        "customers", "generation_runs", "payment_transactions", "public_manifest.json"]
    assert (tmp_path / "out" / "customers" / "customers.parquet").read_bytes() == b"PAR1"


def test_restaging_replaces_previous_run(tmp_path):
    stage(fake_run(tmp_path / "public"), tmp_path / "out")
    (tmp_path / "out" / "stale").mkdir()
    stage(tmp_path / "public", tmp_path / "out")
    assert not (tmp_path / "out" / "stale").exists()


def test_incomplete_run_refused(tmp_path):
    with pytest.raises(SystemExit, match="incomplete"):
        stage(fake_run(tmp_path / "public", complete=False), tmp_path / "out")


def test_private_output_refused(tmp_path):
    run = fake_run(tmp_path / "public")
    (run / "scenario_ground_truth.parquet").write_bytes(b"PAR1")
    with pytest.raises(SystemExit, match="private"):
        stage(run, tmp_path / "out")
    assert not (tmp_path / "out").exists()
