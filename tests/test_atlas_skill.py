"""E4: the atlas skill gives the same answers over the stub and Atlas REST."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills" / "atlas" / "src"))
sys.path.insert(0, str(REPO / "tests"))
pytest.importorskip("pandas")

import inspect  # noqa: E402

import atlas  # noqa: E402
from sync_skill import Sync  # noqa: E402
from services.common.metadata import AtlasBackend, StubBackend  # noqa: E402
from test_metadata import STUB, fake_atlas_client  # noqa: E402


@pytest.fixture(params=["stub", "atlas"])
def catalog(request, monkeypatch):
    monkeypatch.setattr(atlas, "_backend", StubBackend(STUB) if request.param == "stub"
                        else AtlasBackend("http://atlas.test", "u", "p", client=fake_atlas_client()))
    return Sync(atlas)


def test_public_functions_are_async():
    assert all(inspect.iscoroutinefunction(getattr(atlas, n)) for n in atlas.__all__)


def test_search(catalog):
    hits = catalog.search("routing")
    assert "routing_rules" in set(hits["name"])
    assert list(hits.columns) == ["qualified_name", "name", "owner", "classifications", "description"]


def test_entity_by_bare_qualified_and_full_name(catalog):
    for name in ("payment_transactions", "meridian_financial.payment_transactions",
                 "meridian_financial.payment_transactions@local"):
        e = catalog.entity(name)
        assert e["qualified_name"] == "meridian_financial.payment_transactions@local"
        assert {"name": "routing_rule_id", "type": "bigint"} in e["columns"] and len(e["columns"]) == 28


def test_lineage_shows_loads_and_join_keys(catalog):
    lin = catalog.lineage("payment_transactions")
    assert "staging.payment_transactions_parquet@local" in lin["inputs"]
    assert "meridian_financial.routing_rules@local" in lin["inputs"]
    edges = {(p["from"], p["to"]) for p in lin["processes"]}
    assert ("meridian_financial.routing_rules@local", "meridian_financial.payment_transactions@local") in edges


def test_classifications(catalog):
    assert "Finance" in catalog.classifications("payment_transactions")


def test_unknown_table_names_close_matches(catalog):
    with pytest.raises(LookupError, match="payment_transactions"):
        catalog.entity("payment_transaction")


def test_private_database_is_not_resolvable(catalog):
    with pytest.raises(LookupError):
        catalog.entity("meridian_ground_truth.scenario_ground_truth")
