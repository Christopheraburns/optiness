"""Metadata backend: Apache Atlas, or a local stub with the same interface.

``METADATA_BACKEND=atlas`` talks to Atlas REST v2 with basic auth.
``METADATA_BACKEND=stub`` reads ``config/atlas-stub.json`` so Docker Desktop
runs need no Atlas. Both return the same plain dict shapes, so the ``atlas``
skill and the console never branch on which one is active.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .env import AtlasSettings, settings


@dataclass
class Entity:
    guid: str
    type_name: str
    qualified_name: str
    name: str
    description: str
    owner: str
    columns: list[dict[str, str]]
    classifications: list[str]

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class Lineage:
    qualified_name: str
    inputs: list[str]
    outputs: list[str]
    processes: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class MetadataBackend(Protocol):
    def search(self, term: str, limit: int = 20) -> list[Entity]: ...
    def entity(self, qualified_name: str) -> Entity | None: ...
    def lineage(self, qualified_name: str, depth: int = 3) -> Lineage | None: ...


# --------------------------------------------------------------------- stub
class StubBackend:
    """Reads a JSON catalog: {"entities": {qualified_name: {...}}, "lineage": [...]}."""

    def __init__(self, path: Path):
        self.path = path
        self._doc: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        if self._doc is None:
            self._doc = json.loads(self.path.read_text()) if self.path.exists() else {"entities": {}}
        return self._doc

    @staticmethod
    def _to_entity(qn: str, raw: dict[str, Any]) -> Entity:
        return Entity(
            guid=raw.get("guid", qn),
            type_name=raw.get("typeName", "impala_table"),
            qualified_name=qn,
            name=raw.get("name", qn.split("@")[0].split(".")[-1]),
            description=raw.get("description", ""),
            owner=raw.get("owner", ""),
            columns=list(raw.get("columns", [])),
            classifications=list(raw.get("classifications", [])),
        )

    def search(self, term: str, limit: int = 20) -> list[Entity]:
        term_l = term.lower()
        hits = []
        for qn, raw in self._load().get("entities", {}).items():
            hay = " ".join([qn, raw.get("name", ""), raw.get("description", ""),
                            " ".join(c.get("name", "") for c in raw.get("columns", []))]).lower()
            if term_l in hay:
                hits.append(self._to_entity(qn, raw))
        return hits[:limit]

    def entity(self, qualified_name: str) -> Entity | None:
        raw = self._load().get("entities", {}).get(qualified_name)
        return self._to_entity(qualified_name, raw) if raw else None

    def lineage(self, qualified_name: str, depth: int = 3) -> Lineage | None:
        if self.entity(qualified_name) is None:
            return None
        edges = self._load().get("lineage", [])
        inputs = [e["from"] for e in edges if e.get("to") == qualified_name]
        outputs = [e["to"] for e in edges if e.get("from") == qualified_name]
        processes = [e for e in edges if qualified_name in (e.get("from"), e.get("to"))]
        return Lineage(qualified_name=qualified_name, inputs=inputs, outputs=outputs, processes=processes)


# -------------------------------------------------------------------- atlas
class AtlasBackend:
    """Apache Atlas REST v2. Table type defaults to impala_table; hive_table
    entities are returned by the same search."""

    def __init__(self, url: str, user: str, password: str, *, client: Any | None = None, timeout: float = 30.0):
        import httpx  # lazy: the stub path must not need it

        self.base = url.rstrip("/")
        self.client = client or httpx.Client(base_url=self.base, auth=(user, password), timeout=timeout)

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        r = self.client.get(path, params=params)
        r.raise_for_status()
        return r.json()

    def _entity_from_atlas(self, raw: dict[str, Any]) -> Entity:
        attrs = raw.get("attributes", {})
        cols = []
        for c in attrs.get("columns", []) or []:
            ca = c.get("attributes", {}) if isinstance(c, dict) else {}
            cols.append({"name": ca.get("name") or c.get("displayText", ""), "type": ca.get("type", "")})
        return Entity(
            guid=raw.get("guid", ""),
            type_name=raw.get("typeName", ""),
            qualified_name=attrs.get("qualifiedName", ""),
            name=attrs.get("name", ""),
            description=attrs.get("description") or "",
            owner=attrs.get("owner") or "",
            columns=cols,
            classifications=[c.get("typeName", "") for c in raw.get("classifications", []) or []],
        )

    def search(self, term: str, limit: int = 20) -> list[Entity]:
        doc = self._get("/api/atlas/v2/search/basic", query=term, limit=limit, excludeDeletedEntities="true")
        return [self._entity_from_atlas(e) for e in doc.get("entities", []) or []]

    def entity(self, qualified_name: str, type_name: str = "impala_table") -> Entity | None:
        try:
            doc = self._get(
                f"/api/atlas/v2/entity/uniqueAttribute/type/{type_name}",
                **{"attr:qualifiedName": qualified_name},
            )
        except Exception:  # noqa: BLE001 - a 404 is the normal miss
            return None
        return self._entity_from_atlas(doc.get("entity", {}))

    def lineage(self, qualified_name: str, depth: int = 3) -> Lineage | None:
        ent = self.entity(qualified_name)
        if ent is None:
            return None
        doc = self._get(f"/api/atlas/v2/lineage/{ent.guid}", depth=depth, direction="BOTH")
        guid_to_qn = {g: e.get("attributes", {}).get("qualifiedName", g) for g, e in (doc.get("guidEntityMap") or {}).items()}
        inputs, outputs, processes = [], [], []
        for rel in doc.get("relations", []) or []:
            frm, to = guid_to_qn.get(rel.get("fromEntityId")), guid_to_qn.get(rel.get("toEntityId"))
            processes.append({"from": frm, "to": to})
            if to == qualified_name:
                inputs.append(frm)
            if frm == qualified_name:
                outputs.append(to)
        return Lineage(qualified_name=qualified_name, inputs=inputs, outputs=outputs, processes=processes)


def backend(cfg: AtlasSettings | None = None) -> MetadataBackend:
    cfg = cfg or settings().atlas
    if cfg.backend == "atlas":
        if not cfg.url:
            raise RuntimeError("METADATA_BACKEND=atlas requires ATLAS_URL")
        return AtlasBackend(cfg.url, cfg.user, cfg.password)
    return StubBackend(cfg.stub_path)
