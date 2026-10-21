"""C3 acceptance: stub and Atlas backends pass the same tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.common.metadata import AtlasBackend, StubBackend  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
STUB = REPO / "config" / "atlas-stub.json"
QN = "meridian_financial.payment_transactions@local"


def fake_atlas_client():
    """An httpx client whose transport answers like Atlas REST v2 from the stub doc."""
    import httpx

    doc = json.loads(STUB.read_text())

    def atlas_entity(qn: str) -> dict:
        raw = doc["entities"][qn]
        return {
            "guid": raw["guid"],
            "typeName": raw["typeName"],
            "attributes": {
                "qualifiedName": qn,
                "name": raw["name"],
                "description": raw["description"],
                "owner": raw["owner"],
                "columns": [{"attributes": {"name": c["name"], "type": c["type"]}} for c in raw["columns"]],
            },
            "classifications": [{"typeName": c} for c in raw["classifications"]],
        }

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        q = dict(request.url.params)
        if p == "/api/atlas/v2/search/basic":
            term = q["query"].lower()
            hits = [atlas_entity(qn) for qn in doc["entities"] if term in qn.lower()]
            return httpx.Response(200, json={"entities": hits})
        if p.startswith("/api/atlas/v2/entity/uniqueAttribute/type/"):
            qn = q["attr:qualifiedName"]
            if qn not in doc["entities"]:
                return httpx.Response(404, json={"errorCode": "ATLAS-404-00-005"})
            return httpx.Response(200, json={"entity": atlas_entity(qn)})
        if p.startswith("/api/atlas/v2/lineage/"):
            guid = p.rsplit("/", 1)[1]
            qn = next(k for k, v in doc["entities"].items() if v["guid"] == guid)
            guid_of = {k: v["guid"] for k, v in doc["entities"].items()}
            rels, gmap = [], {}
            for e in doc["lineage"]:
                if qn in (e["from"], e["to"]):
                    f, t = guid_of.get(e["from"], e["from"]), guid_of.get(e["to"], e["to"])
                    rels.append({"fromEntityId": f, "toEntityId": t})
                    gmap[f] = {"attributes": {"qualifiedName": e["from"]}}
                    gmap[t] = {"attributes": {"qualifiedName": e["to"]}}
            return httpx.Response(200, json={"relations": rels, "guidEntityMap": gmap})
        return httpx.Response(404)

    return httpx.Client(base_url="http://atlas.test", transport=httpx.MockTransport(handler))


@pytest.fixture(params=["stub", "atlas"])
def backend(request):
    if request.param == "stub":
        return StubBackend(STUB)
    return AtlasBackend("http://atlas.test", "u", "p", client=fake_atlas_client())


def test_entity_roundtrip(backend):
    e = backend.entity(QN)
    assert e is not None
    assert e.name == "payment_transactions"
    assert {"name": "routing_rule_id", "type": "bigint"} in e.columns
    assert {"name": "amount_usd", "type": "decimal(18,2)"} in e.columns
    assert "Finance" in e.classifications


def test_missing_entity_is_none(backend):
    assert backend.entity("meridian_financial.nope@local") is None


def test_search_finds_by_name(backend):
    names = {e.name for e in backend.search("routing_rules")}
    assert "routing_rules" in names


def test_private_database_not_cataloged(backend):
    assert backend.search("ground_truth") == []


def test_lineage_shape(backend):
    lin = backend.lineage(QN)
    assert lin is not None
    assert "staging.payment_transactions_parquet@local" in lin.inputs
    assert "meridian_financial.routing_rules@local" in lin.inputs
    assert lin.outputs == []
