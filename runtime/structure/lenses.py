"""Serving-lens vocabulary (RFC 0006 §7① / Pillar ①).

Reads `schema/data/lenses.yaml` — the single source for the lens → (kind, domain)
selectors — and exposes matching + coverage helpers. Like `resolver.py`, this is
the ONE place the lens vocabulary is derived; callers (the recall/search MCP
surface in Pillar ③, the verifier's P1 rubric) never hardcode a lens list.

A lens is a DEFAULT query-time filter, never a storage boundary — the `full`
lens (`{kind: "*", domain: "*"}`) always exists so cross-domain joins stay
possible ("one vault, lenses over walls").
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import yaml

LENSES_YAML = Path(__file__).resolve().parents[2] / "schema" / "data" / "lenses.yaml"

_WILDCARD = "*"


@functools.lru_cache(maxsize=1)
def _data() -> Dict[str, Any]:
    return yaml.safe_load(LENSES_YAML.read_text())


def lens_names() -> Tuple[str, ...]:
    return tuple(_data()["lenses"].keys())


def default_lens() -> str:
    return _data()["default_lens"]


def selectors(name: str) -> List[Dict[str, str]]:
    lenses = _data()["lenses"]
    if name not in lenses:
        raise KeyError(f"unknown lens {name!r} (have {sorted(lenses)})")
    return list(lenses[name]["selectors"])


def matches(name: str, kind: str, domain: str) -> bool:
    """Does lens `name` admit a node of (kind, domain)? A selector matches when
    each axis equals the node's value or is the `*` wildcard.

    For entities, `domain` is one value drawn from the `in_scheme` LIST — the
    caller tests membership by calling this per in_scheme value."""
    for sel in selectors(name):
        k, d = sel.get("kind", _WILDCARD), sel.get("domain", _WILDCARD)
        if (k == _WILDCARD or k == kind) and (d == _WILDCARD or d == domain):
            return True
    return False


def lenses_admitting(kind: str, domain: str) -> List[str]:
    """Every lens that admits (kind, domain) — used by the coverage validator."""
    return [n for n in lens_names() if matches(n, kind, domain)]


def validate_coverage(observed_pairs: Iterable[Tuple[str, str]]) -> Dict[str, Any]:
    """The Pillar ① gate over the vocabulary + the vault's actual (kind, domain)
    pairs. Two invariants:

    1. **Completeness** — every observed pair is admitted by at least one lens
       (the `full` lens guarantees this unless someone narrows it).
    2. **dev excludes personal** — the whole point of the dev lens (RFC 0006 §7):
       a coding session must not see personal-domain nodes. Any (kind, personal)
       admitted by `dev` is a leak.

    Returns `{ok, uncovered, dev_personal_leaks}`; `ok` is the gate."""
    pairs = sorted(set(observed_pairs))
    uncovered = [p for p in pairs if not lenses_admitting(*p)]
    dev_leaks = [p for p in pairs if p[1] == "personal" and matches("dev", *p)]
    return {
        "ok": not uncovered and not dev_leaks,
        "uncovered": uncovered,
        "dev_personal_leaks": dev_leaks,
    }
