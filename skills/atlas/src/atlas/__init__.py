"""Read-only metadata catalog: Apache Atlas, or the local stub, behind one API.

All functions are async (the prime-agent skill convention):

    hits = await atlas.search(term, limit=20)      # DataFrame
    t = await atlas.entity(table)                  # dict: description, owner, columns, classifications
    lin = await atlas.lineage(table)               # dict: inputs, outputs, processes
    tags = await atlas.classifications(table)      # list[str]

services.common.metadata picks the backend from METADATA_BACKEND, so this skill
never branches on it. Table names are resolved to Atlas qualified names through
the backend's own search, which keeps the cluster suffix (`@local` here,
the cluster name on Cloudera) out of the agent's code.
"""
from __future__ import annotations

import asyncio as _asyncio
import difflib as _difflib
import sys as _sys
from pathlib import Path as _Path

_REPO = _Path(__file__).resolve().parents[4]  # skills/atlas/src/atlas/__init__.py
if str(_REPO) not in _sys.path:
    _sys.path.insert(0, str(_REPO))

import pandas as _pd  # noqa: E402

from services.common.env import settings as _settings  # noqa: E402
from services.common.metadata import MetadataBackend as _MetadataBackend  # noqa: E402
from services.common.metadata import backend as _make_backend  # noqa: E402

__all__ = ["search", "entity", "lineage", "classifications"]

_backend: _MetadataBackend | None = None


def _catalog() -> _MetadataBackend:
    global _backend
    if _backend is None:
        _backend = _make_backend()
    return _backend


def _qualified(table: str) -> str:
    """Bare name, db.table, or db.table@cluster -> the catalog's qualified name."""
    name = table.strip().strip("`")
    if "@" in name:
        return name
    db, _, bare = name.rpartition(".")
    prefix = f"{db or _settings().impala.database}.{bare}@".lower()
    hits = [e.qualified_name for e in _catalog().search(bare, limit=200)]
    for qn in hits:
        if qn.lower().startswith(prefix):
            return qn
    known = [e.qualified_name for e in _catalog().search(db or _settings().impala.database, limit=200)]
    close = _difflib.get_close_matches(name, [q.split("@")[0] for q in known], n=3, cutoff=0.5)
    raise LookupError(f"no catalog entry for {table!r}" + (f"; closest: {close}" if close else ""))


async def search(term: str, limit: int = 20) -> _pd.DataFrame:
    """Tables whose name, description or columns contain ``term``.

    Returns a DataFrame: qualified_name, name, owner, classifications (list), description.
    """
    found = await _asyncio.to_thread(_catalog().search, term, limit)
    rows = [{"qualified_name": e.qualified_name, "name": e.name, "owner": e.owner,
             "classifications": e.classifications, "description": e.description}
            for e in found]
    return _pd.DataFrame(rows, columns=["qualified_name", "name", "owner", "classifications", "description"])


async def entity(table: str) -> dict:
    """Description, owner, columns and classifications of one table.

    ``table``: bare name, db.table, or db.table@cluster. Returns
    {"qualified_name": str, "name": str, "description": str, "owner": str,
     "columns": [{"name": str, "type": str}], "classifications": [str]}.
    """
    qn = await _asyncio.to_thread(_qualified, table)
    found = await _asyncio.to_thread(_catalog().entity, qn)
    if found is None:
        raise LookupError(f"no catalog entry for {table!r} ({qn})")
    out = found.to_dict()
    out.pop("guid", None)
    out.pop("type_name", None)
    return out


async def lineage(table: str, depth: int = 3) -> dict:
    """Inputs, outputs and the edges between them for one table.

    Returns {"qualified_name": str, "inputs": [qualified name str], "outputs": [qualified name str],
     "processes": [{"from": str, "to": str, ...}]}. Inputs and outputs are plain strings.
    """
    qn = await _asyncio.to_thread(_qualified, table)
    found = await _asyncio.to_thread(_catalog().lineage, qn, depth)
    if found is None:
        raise LookupError(f"no lineage for {table!r}")
    return found.to_dict()


async def classifications(table: str) -> list[str]:
    """Classification names on one table, e.g. ["Finance", "PCI_Scope"]."""
    return list((await entity(table))["classifications"])
