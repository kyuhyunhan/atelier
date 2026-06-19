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
    """Top dir holding ingested content (today: `raw`, post-P2 flip).

    This is THE one constant for the content tree: homes (learning_*), intake
    dirs, and overlay path patterns all compose their prefix from this value
    via `{content_root}` / `home()` / `intake_dir()`, so flipping the root is a
    single edit in structure.yaml.
    """
    return _data()["roots"]["content_root"]


def legacy_content_root() -> str:
    """Pre-flip name of the content root (today: `provenance`).

    Writers resolve to `content_root()` but fall back to this name when only the
    un-renamed tree is on disk (the RFC 0003 provenance/<-raw/ transition). The
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


def legacy_graph_root() -> str:
    """Pre-rename name of the graph root (today: `wiki`), from prefix_aliases.

    Mirrors `legacy_content_root()` for the RFC 0003 wiki/->graph/ rename, so the
    legacy graph prefix lives in ONE place and is never re-hardcoded.
    """
    alias = _data()["prefix_aliases"].get(f"{graph_root()}/")
    if not alias:
        raise KeyError("no legacy alias for graph_root in prefix_aliases")
    return alias.rstrip("/")


def expand_content_root(value: str) -> str:
    """Expand a `{content_root}` placeholder in a structural path string.

    The ONE composition point for content-rooted schema data (homes,
    overlay path_patterns, inbox.path). Strings without the placeholder pass
    through unchanged, so callers may pass already-absolute structural paths.
    """
    return value.replace("{content_root}", content_root())


# --- Structural prefixes (for SQL LIKE sets, lint L1/L5) ------------------
def content_prefixes() -> Tuple[str, ...]:
    """Top-level content-tree slug prefixes — new + legacy — as `dir/` strings.

    The single source for "is this slug/link in the content tree?" matching
    (lint L1). Carries both the live `content_root()/` and the retired
    `legacy_content_root()/` form so legacy vaults still match. NO call site
    should hardcode `raw/` / `provenance/`.
    """
    return (f"{content_root()}/", f"{legacy_content_root()}/")


def graph_prefixes() -> Tuple[str, ...]:
    """Top-level graph-tree slug prefixes — new + legacy — as `dir/` strings.

    The single source for "is this page in the graph tree?" matching (lint
    L1/L5). Carries both `graph_root()/` and the retired `legacy_graph_root()/`
    (`wiki/`). NO call site should hardcode `graph/` / `wiki/`.
    """
    return (f"{graph_root()}/", f"{legacy_graph_root()}/")


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
    """Vault-relative write dir for a node `page_type`.

    A home value may carry a `{content_root}` placeholder; it is expanded from
    `content_root()` so the learning_* trees (and any future content-rooted
    home) compose their prefix from the ONE root constant. Flipping
    roots.content_root moves the whole content tree with a single edit.
    """
    homes = _data()["homes"]
    if page_type not in homes:
        raise KeyError(f"no home for page_type: {page_type!r}")
    return expand_content_root(homes[page_type])


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
