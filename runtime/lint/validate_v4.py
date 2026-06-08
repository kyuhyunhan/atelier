"""Schema v4 frontmatter validator.

Driven entirely by `schema/data/base.yaml` + the matching overlay
(librarian.overlay.yaml, builder.overlay.yaml, learnings.overlay.yaml).
Returns a list of Findings shaped like the rest of the lint pipeline,
but is invoked separately so non-frontmatter rules (L1/L3/L5/L6) can
stay independent.

Replaces the proto-engine's `gorae validate` (Schema v3). The corpus
invariant — every entry_id unique — is also enforced when given a full
file set rather than a subset.
"""
from __future__ import annotations

import uuid as _uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

from ..index import parse as _parse
from ..util import config as _config
from .runner import Finding


_SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schema" / "data"


def _load_overlay(name: str) -> Dict[str, Any]:
    path = _SCHEMA_DIR / f"{name}.overlay.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _all_overlays() -> List[Dict[str, Any]]:
    return [
        _load_overlay("librarian"),
        _load_overlay("builder"),
        _load_overlay("learnings"),
    ]


def page_type_rules() -> List[Tuple[str, str]]:
    """All (path_pattern, page_type) pairs across overlays, declaration order.

    Single source of truth for BOTH frontmatter validation (`_match_page_type`)
    and page classification (`runtime/index/classify.py`). Hard-rule #3: page
    types are schema *data* (schema/data/*.overlay.yaml), never hardcoded in
    runtime code. Declaration order encodes specificity — more specific
    patterns must be declared before broader globs.
    """
    rules: List[Tuple[str, str]] = []
    for overlay in _all_overlays():
        for ptype, spec in (overlay.get("page_types") or {}).items():
            pattern = spec.get("path_pattern")
            if pattern:
                rules.append((pattern, ptype))
    return rules


def _is_uuid(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        _uuid.UUID(value)
        return True
    except ValueError:
        return False


def _match_page_type(rel_path: str, overlays: Iterable[Dict[str, Any]]
                    ) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Pick the first overlay page_type whose path_pattern matches.

    The pattern uses `**` and `*` (POSIX-glob style). Most specific first
    is the overlay author's responsibility — we walk in declaration
    order.
    """
    import fnmatch
    for overlay in overlays:
        for ptype, spec in (overlay.get("page_types") or {}).items():
            pattern = spec.get("path_pattern")
            if not pattern:
                continue
            if _glob_match(pattern, rel_path) or fnmatch.fnmatchcase(rel_path, pattern):
                return ptype, spec
    return None, None


def _glob_match(pattern: str, rel: str) -> bool:
    import re
    if "**" not in pattern:
        return False
    rx = re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
    return bool(re.fullmatch(rx, rel))


def _check_field_spec(field_name: str, value: Any, spec: Dict[str, Any]
                      ) -> List[str]:
    """Return list of error strings for one frontmatter field."""
    errors: List[str] = []
    expected_type = spec.get("type")
    nullable = bool(spec.get("nullable", False))
    if value is None:
        if not nullable and "const" not in spec and "enum" not in spec:
            errors.append(f"{field_name}: null not allowed")
        return errors

    if "const" in spec and value != spec["const"]:
        errors.append(f"{field_name}: must equal {spec['const']!r}, got {value!r}")
    if "enum" in spec and value not in spec["enum"]:
        errors.append(f"{field_name}: must be one of {spec['enum']}, got {value!r}")

    if expected_type == "integer" and not isinstance(value, int):
        errors.append(f"{field_name}: must be integer, got {type(value).__name__}")
    if expected_type == "string" and not isinstance(value, str):
        errors.append(f"{field_name}: must be string, got {type(value).__name__}")
    if expected_type == "array" and not isinstance(value, list):
        errors.append(f"{field_name}: must be array, got {type(value).__name__}")
    if expected_type == "object" and not isinstance(value, dict):
        errors.append(f"{field_name}: must be object, got {type(value).__name__}")

    if expected_type == "string" and isinstance(value, str):
        pattern = spec.get("pattern")
        if pattern:
            import re
            if not re.fullmatch(pattern, value):
                errors.append(f"{field_name}: does not match pattern {pattern!r}")
        if spec.get("format") == "uuid-v5" and not _is_uuid(value):
            errors.append(f"{field_name}: not a valid UUID")
        if spec.get("format") == "date" and not _looks_like_date(value):
            errors.append(f"{field_name}: not a YYYY-MM-DD date")
    if expected_type == "integer" and isinstance(value, int):
        if "minimum" in spec and value < spec["minimum"]:
            errors.append(f"{field_name}: below minimum {spec['minimum']}")
    return errors


def _looks_like_date(s: str) -> bool:
    import re
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", s))


def _validate_one(path: Path, rel_path: str,
                  overlays: List[Dict[str, Any]]) -> List[str]:
    text = path.read_text(encoding="utf-8")
    fm, _body = _parse.split_frontmatter(text)
    errors: List[str] = []

    if fm.get("schema_version") not in (4, 5):
        errors.append(
            f"schema_version: must be 4 or 5, got {fm.get('schema_version')!r}")
    if not _is_uuid(fm.get("entry_id")) and fm.get("entry_id") != "PENDING":
        errors.append(f"entry_id: must be a valid UUID, got {fm.get('entry_id')!r}")

    ptype, spec = _match_page_type(rel_path, overlays)
    if spec is None:
        return errors  # no overlay claims this path — minimal check only.

    required = spec.get("required_fields") or []
    for f in required:
        if f not in fm or fm.get(f) in (None, ""):
            errors.append(f"missing required field: {f}")

    for fname, fspec in (spec.get("field_specs") or {}).items():
        if fname in fm:
            errors.extend(_check_field_spec(fname, fm[fname], fspec))

    return errors


def validate_paths(paths: List[Path], *, vault_root: Path,
                   fail_fast: bool = False) -> List[Finding]:
    overlays = _all_overlays()
    findings: List[Finding] = []
    seen_entry_ids: Dict[str, List[Path]] = defaultdict(list)

    for p in paths:
        try:
            rel = str(p.resolve().relative_to(vault_root.resolve()))
        except ValueError:
            rel = p.name
        errors = _validate_one(p, rel, overlays)
        from ..index import parse as _parse_inner
        try:
            fm, _ = _parse_inner.split_frontmatter(p.read_text(encoding="utf-8"))
            eid = fm.get("entry_id")
            if isinstance(eid, str) and eid and eid != "PENDING":
                seen_entry_ids[eid].append(p)
        except Exception:
            pass

        for e in errors:
            findings.append(Finding(rule_id="V0", severity="FAIL",
                                     message=e, page_slug=rel))
        if fail_fast and errors:
            break

    # Corpus uniqueness check.
    for eid, ps in seen_entry_ids.items():
        if len(ps) > 1:
            findings.append(Finding(
                rule_id="V1",
                severity="FAIL",
                message=f"duplicate entry_id {eid} in {len(ps)} files",
                page_slug=None,
                details={"paths": [str(x) for x in ps]},
            ))
    return findings


def validate_vault(role: str = "librarian-territory",
                   *, fail_fast: bool = False) -> List[Finding]:
    cfg = _config.load()
    if cfg.vault is not None:
        root = cfg.vault.local
    else:
        root = cfg.space_by_role(role).local
    paths = sorted(root.rglob("*.md"))
    return validate_paths(paths, vault_root=root, fail_fast=fail_fast)
