"""D1–D6 system-health checks. Each is independent and pure."""
from __future__ import annotations

import sqlite3
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..util import config, fs


@dataclass
class Diagnosis:
    id: str
    name: str
    severity: str  # OK | WARN | FAIL
    message: str
    details: Dict[str, Any] = field(default_factory=dict)


# The DB schema_version the engine writes (schema/db/sql/0001_initial.sql seed).
# Bump in lockstep with that seed; D1 fails loudly on a mismatch so a stale DB is
# caught. v6 = RFC 0003 (provenance column).
EXPECTED_SCHEMA_VERSION = "6"


def D1_db_present(cfg: config.Config) -> Diagnosis:
    """D1: SQLite cache exists and meta.schema_version matches expected."""
    from ..util import db
    if not config.DB_PATH.exists():
        return Diagnosis("D1", "db-present", "FAIL",
                         "atelier.db missing — run `atelier reindex --full`",
                         {"path": str(config.DB_PATH)})
    conn = db.connect()
    try:
        sv = db.get_meta(conn, "schema_version")
        if sv != EXPECTED_SCHEMA_VERSION:
            return Diagnosis("D1", "db-present", "FAIL",
                             f"schema_version={sv}, expected {EXPECTED_SCHEMA_VERSION}"
                             " — run `atelier reindex --full`",
                             {"actual": sv, "expected": EXPECTED_SCHEMA_VERSION})
        return Diagnosis("D1", "db-present", "OK", f"schema_version={sv}")
    finally:
        conn.close()


def D2_filesystem_drift(cfg: config.Config) -> Diagnosis:
    """D2: do indexed slugs match the filesystem?"""
    from ..util import db
    from ..index.reindex import canonical_spaces
    conn = db.connect()
    drifted: list[tuple[str, str, str]] = []
    try:
        # Compare only the canonical (deduped) spaces — the same set
        # reindex writes. Iterating every synthesized pseudo-space would
        # report the whole vault as phantom drift under the un-indexed name.
        for space_name in canonical_spaces(cfg):
            sp = cfg.spaces[space_name]
            if not sp.local.exists():
                continue
            on_disk = {fs.slug_for(sp.local, p) for p in fs.walk_indexable(sp.local)}
            in_db = {r["slug"] for r in conn.execute(
                "SELECT slug FROM pages WHERE space=?", (space_name,))}
            for slug in on_disk - in_db:
                drifted.append((space_name, slug, "on disk, not in db"))
            for slug in in_db - on_disk:
                drifted.append((space_name, slug, "in db, not on disk"))
    finally:
        conn.close()
    if not drifted:
        return Diagnosis("D2", "fs-drift", "OK", "filesystem and DB agree")
    return Diagnosis("D2", "fs-drift", "WARN",
                     f"{len(drifted)} drift entries — run `atelier reindex --full`",
                     {"count": len(drifted), "sample": drifted[:5]})


def D3_voice_overlay(cfg: config.Config) -> Diagnosis:
    """D3: every configured voice-overlay file exists (warns if missing).

    RFC 0001 retired the fixed librarian/builder agent names — this iterates
    whatever voices the config declares, so it is persona-neutral."""
    missing: list[str] = []
    for _name, spec in (cfg.raw.get("agents") or {}).items():
        overlay = (spec or {}).get("voice_overlay")
        if not overlay:
            continue
        p = Path(overlay).expanduser()
        if not p.exists():
            missing.append(str(p))
    if missing:
        return Diagnosis("D3", "voice-overlay", "WARN",
                         f"{len(missing)} voice overlay file(s) missing",
                         {"missing": missing})
    return Diagnosis("D3", "voice-overlay", "OK", "voice overlays present")


def D4_git_remote(cfg: config.Config) -> Diagnosis:
    """D4: each space's local git repo has its configured remote."""
    bad: list[tuple[str, str]] = []
    for name, sp in cfg.spaces.items():
        if not sp.local.exists() or not (sp.local / ".git").exists():
            continue
        if not sp.remote_url:
            continue
        try:
            out = subprocess.check_output(
                ["git", "-C", str(sp.local), "remote", "-v"],
                stderr=subprocess.DEVNULL, text=True,
            )
        except subprocess.CalledProcessError:
            bad.append((name, "git remote -v failed"))
            continue
        if sp.remote_url.replace("https://", "").replace("http://", "") not in out:
            bad.append((name, f"configured remote {sp.remote_url} not present"))
    if bad:
        return Diagnosis("D4", "git-remote", "WARN", f"{len(bad)} remote(s) drifted",
                         {"issues": bad})
    return Diagnosis("D4", "git-remote", "OK", "git remotes match config")


def D5_asset_index(cfg: config.Config) -> Diagnosis:
    """D5: stub — verify embedded_assets references resolve. Real impl in v0.2."""
    return Diagnosis("D5", "asset-index", "OK",
                     "stub (full asset/R2 verification deferred to v0.2)")


def D6_orphan_chunks(cfg: config.Config) -> Diagnosis:
    """D6: chunks_fts rows without a matching chunks row (FTS desync)."""
    from ..util import db
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM chunks_fts "
            "WHERE rowid NOT IN (SELECT id FROM chunks)"
        ).fetchone()
        n = row["n"]
        if n > 0:
            return Diagnosis("D6", "fts-desync", "FAIL",
                             f"{n} orphan FTS rows — DB needs rebuild",
                             {"n": n})
        return Diagnosis("D6", "fts-desync", "OK", "FTS in sync with chunks")
    finally:
        conn.close()


# D7 (by-project mirror drift) was retired with the mirror itself (RFC 0001):
# learnings are a flat, facet-classified store, so there is no mirror to drift.


ALL_CHECKS = [D1_db_present, D2_filesystem_drift, D3_voice_overlay,
              D4_git_remote, D5_asset_index, D6_orphan_chunks]


def run_all(cfg: config.Config) -> List[Diagnosis]:
    return [check(cfg) for check in ALL_CHECKS]
