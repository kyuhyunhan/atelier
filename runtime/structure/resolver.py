"""Runtime resolver over schema/data/structure.yaml (RFC 0005 P1).

Mirrors runtime/lint/loader.py: yaml.safe_load the data file, cache it, expose
typed accessors. This is the ONE place vault paths and entry_ids are derived;
P1.2 migrates call sites onto this API.

entry_id() always uses Python stock uuid.NAMESPACE_DNS — the single namespace
for all nodes. For existing kinds the discriminator templates reproduce the
current stored strings byte-for-byte (snapshot-locked in tests).
"""
from __future__ import annotations

import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Tuple

import yaml

STRUCTURE_YAML = (
    Path(__file__).resolve().parents[2] / "schema" / "data" / "structure.yaml"
)

# The only uuid5 namespace, referenced by the `namespace:` marker in the yaml.
_NAMESPACE = {"uuid.NAMESPACE_DNS": uuid.NAMESPACE_DNS}


@lru_cache(maxsize=1)
def _data() -> Dict[str, Any]:
    return yaml.safe_load(STRUCTURE_YAML.read_text())


# --- Roots ---------------------------------------------------------------
def content_root() -> str:
    """Top dir holding ingested/provenance content (today: `provenance`)."""
    return _data()["roots"]["content_root"]


def graph_root() -> str:
    """Top dir holding the knowledge graph (entities/themes/sources)."""
    return _data()["roots"]["graph_root"]


# --- Intake ---------------------------------------------------------------
def intake_dir(domain: str) -> str:
    """Write dir for an intake `domain`.

    `personal`/`knowledge` resolve under content_root; `workshop` is a vault
    top-level tree. Returns a vault-relative POSIX path.
    """
    intake = _data()["intake"]
    if domain not in intake or domain == "inbox_subpath":
        raise KeyError(f"unknown intake domain: {domain!r}")
    sub = intake[domain]
    if domain == "workshop":
        return sub
    return f"{content_root()}/{sub}"


def inbox_dir() -> str:
    """Ad-hoc capture leaf, under the personal intake dir."""
    return f"{intake_dir('personal')}/{_data()['intake']['inbox_subpath']}"


# --- Homes ----------------------------------------------------------------
def home(page_type: str) -> str:
    """Vault-relative write dir for a node `page_type`."""
    homes = _data()["homes"]
    if page_type not in homes:
        raise KeyError(f"no home for page_type: {page_type!r}")
    return homes[page_type]


# --- Prefix aliasing ------------------------------------------------------
def prefix_aliases() -> Dict[str, str]:
    return dict(_data()["prefix_aliases"])


def known_prefixes() -> Tuple[str, ...]:
    return tuple(_data()["known_prefixes"])


def shorthand_bases() -> Tuple[str, ...]:
    return tuple(_data()["shorthand_bases"])


# --- entry_id derivation --------------------------------------------------
def _namespace() -> uuid.UUID:
    marker = _data()["entry_id"]["namespace"]
    if marker not in _NAMESPACE:
        raise ValueError(f"unknown entry_id namespace marker: {marker!r}")
    return _NAMESPACE[marker]


def entry_id(kind: str, **parts: Any) -> str:
    """uuid5(NAMESPACE_DNS, template(kind).format(**parts)) -> str.

    For existing kinds this reproduces today's stored entry_id byte-for-byte.
    """
    templates = _data()["entry_id"]["templates"]
    if kind not in templates:
        raise KeyError(f"no entry_id template for kind: {kind!r}")
    discriminator = templates[kind].format(**parts)
    return str(uuid.uuid5(_namespace(), discriminator))
