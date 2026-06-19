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


def legacy_content_root() -> str:
    """Pre-rename name of the content root (today: `raw`).

    Writers resolve to `content_root()` but fall back to this name when only the
    un-renamed tree is on disk (the RFC 0003 raw/->provenance/ transition). The
    name is derived from `prefix_aliases` so the legacy constant lives in ONE
    place, never re-hardcoded at a call site.
    """
    alias = _data()["prefix_aliases"].get(f"{content_root()}/")
    if not alias:
        raise KeyError("no legacy alias for content_root in prefix_aliases")
    return alias.rstrip("/")


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


def intake_subpath(domain: str) -> str:
    """The raw intake `sub` for a domain, RELATIVE to its base (content_root for
    personal/knowledge, vault root for workshop). e.g. personal -> `personal`."""
    intake = _data()["intake"]
    if domain not in intake or domain == "inbox_subpath":
        raise KeyError(f"unknown intake domain: {domain!r}")
    return intake[domain]


def inbox_subpath() -> str:
    """Legacy leaf name under the personal intake dir (today: `inbox`).

    Retained only for the un-migrated new_doc `raw` template. New captures land
    in the first-class `inbox` intake domain via `inbox_dir()` / `intake_dir`.
    """
    return _data()["intake"]["inbox_subpath"]


def inbox_dir() -> str:
    """Ad-hoc capture landing dir — the first-class `inbox` intake domain.

    RFC 0005 §3: `inbox` is a sibling of personal/knowledge (today: `raw/inbox`),
    NOT a leaf under personal. A manual capture is domain-*undetermined*; its
    domain is an explicit frontmatter field, never decreed by the landing path.
    """
    return intake_dir("inbox")


# --- Homes ----------------------------------------------------------------
def home(page_type: str) -> str:
    """Vault-relative write dir for a node `page_type`."""
    homes = _data()["homes"]
    if page_type not in homes:
        raise KeyError(f"no home for page_type: {page_type!r}")
    return homes[page_type]


# --- Atomic v7 node trees (RFC 0005 §7.2 atomize nudge) -------------------
def atomic_source_dir() -> str:
    """Vault-relative dir holding v7 Source nodes (today: graph/atomic/sources)."""
    return home("atomic_source")


def atomic_claim_dir() -> str:
    """Vault-relative dir holding v7 Claim nodes (today: graph/atomic/claims)."""
    return home("atomic_claim")


def atomic_entity_dir() -> str:
    """Vault-relative dir holding v7 Entity nodes (today: graph/atomic/entities)."""
    return home("atomic_entity")


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


# RFC 0005 §5 — content parts that are canonicalized (strip().lower()) BEFORE
# they enter the entry_id discriminator, so casing/whitespace variants of the
# same subject/assertion collapse to one id (= the dedup key). Reuses
# reindex._norm semantics (kept here, not imported, to avoid a dependency cycle
# resolver -> index -> resolver). Keyed by (kind, part-name).
_NORMALIZED_PARTS = {
    ("entity", "pref_label"),   # entity id = type | norm(pref_label)  -> dedup
    ("claim", "statement"),     # claim id  = norm(statement) | derived_from
}


def _norm(s: str) -> str:
    """Canonicalize a content part: strip().lower() (== reindex._norm)."""
    return str(s).strip().lower()


def entry_id(kind: str, **parts: Any) -> str:
    """uuid5(NAMESPACE_DNS, template(kind).format(**parts)) -> str.

    For existing kinds this reproduces today's stored entry_id byte-for-byte.
    For v7 content-addressed kinds (RFC 0005 §5) the normalized parts
    (entity.pref_label, claim.statement) are canonicalized first, so the id is
    the dedup key.
    """
    templates = _data()["entry_id"]["templates"]
    if kind not in templates:
        raise KeyError(f"no entry_id template for kind: {kind!r}")
    norm_parts = dict(parts)
    for name, value in parts.items():
        if (kind, name) in _NORMALIZED_PARTS:
            norm_parts[name] = _norm(value)
    discriminator = templates[kind].format(**norm_parts)
    return str(uuid.uuid5(_namespace(), discriminator))
