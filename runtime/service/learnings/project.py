"""Single source of truth for working-dir → project-slug resolution.

Three call paths must agree on "what project is this session":

- **capture** writes the `project_hint` tag (the by-project mirror was retired, RFC 0001),
- **bootstrap** injects the per-project learnings section at session start,
- **recall** boosts per-project hits each turn.

Historically each derived the slug differently (capture honored an
explicit hint + a vault-self guard; bootstrap and recall each took a bare
`basename`). When they disagree, a capture written under one key is looked
up under another and silently never recalled — the exact failure mode the
accepted learning `1446` warns about: *route paths that must agree through
one shared accessor so they cannot diverge.* This module is that accessor,
mirroring `canonical_spaces()` (`runtime/index/reindex.py`) which already
gives *spaces* the same single-source treatment.

Resolution is a layered chain; the first layer that yields a slug wins.
Every layer is LOCAL — project identity is a property of the working tree,
never of a git remote (a remote can be renamed, mirrored, or absent, and
in a monorepo does not map 1:1 to a folder; deriving identity from it
silently re-keys projects). The "folder name ≠ project name" case is
handled by the two explicit local layers (config-map, marker), not by
inspecting a remote.

  1. explicit    — caller-supplied (capture `project_hint`, recall `project`)
  2. config-map  — `learnings.project_map` in ~/.atelier/config.yaml
  3. marker      — a `.atelier-project` file, walking up to the git root
  4. vault-self  — working_dir inside the vault → ``atelier-self``
  5. git-root    — a *linked* git worktree resolves to its MAIN repo's
                   identity, so every worktree of a repo shares one slug
                   (still local-only: reads the worktree's `.git` pointer,
                   never a remote). Primary repos fall through to basename.
  6. basename    — ``Path(working_dir).name`` (the local default)

`known` reports whether any accepted learning carries this project (a facet
query, RFC 0001 — not a by-project directory check), so callers can warn loudly
when a session's captures won't be recalled instead of failing silently.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ...util import config as _config

_MARKER = ".atelier-project"
SELF_SLUG = "atelier-self"


@dataclass(frozen=True)
class ProjectResolution:
    """The resolved project plus provenance for telemetry / loud warnings."""
    slug: Optional[str]      # None only when no working_dir and no explicit hint
    source: str              # explicit|config-map|marker|vault-self|basename|none
    known: bool              # some accepted learning carries this project (facet)


# ── vault root (mirrors the per-module _vault_root helpers) ──────────────────


def _vault_root(cfg: Optional[_config.Config]) -> Optional[Path]:
    if cfg is None:
        return None
    try:
        if cfg.vault is not None:
            return cfg.vault.local
        return cfg.space_by_role("librarian-territory").local
    except Exception:                        # pragma: no cover - misconfigured env
        return None


def _is_known(vault: Optional[Path], slug: Optional[str]) -> bool:
    """True if any accepted learning carries this project (RFC 0001: a facet
    query, not a by-project directory check). DB first; on a cold/missing index
    fall back to a frontmatter scan of the flat store."""
    if not vault or not slug:
        return False
    try:
        from ...util import db as _db
        conn = _db.connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM learning_facets WHERE kind='project' AND value=? "
                "LIMIT 1", (slug,)).fetchone()
            if row is not None:
                return True
        finally:
            conn.close()
    except Exception:
        pass
    # Fallback: scan the flat store's frontmatter (no DB / not yet indexed).
    from . import store as _store
    from ...index import parse as _parse
    for p in _store.iter_accepted_files(Path(vault)):
        try:
            fm, _ = _parse.split_frontmatter(p.read_text(encoding="utf-8"))
        except Exception:               # pragma: no cover
            continue
        if slug in (fm.get("target_project"), fm.get("project_hint")):
            return True
    return False


# ── layer 2: config project_map ──────────────────────────────────────────────


def _project_map(cfg: Optional[_config.Config]) -> dict:
    if cfg is None:
        return {}
    raw: dict[str, Any] = getattr(cfg, "raw", None) or {}
    learnings = raw.get("learnings") or {}
    pm = learnings.get("project_map") or {}
    return pm if isinstance(pm, dict) else {}


def _resolve_path(raw_path: str) -> str:
    try:
        return str(Path(raw_path).expanduser().resolve())
    except (OSError, RuntimeError):          # pragma: no cover - exotic paths
        return str(Path(raw_path).expanduser())


def _match_project_map(cfg: Optional[_config.Config], wd: Path) -> Optional[str]:
    """Exact path match wins; otherwise the longest matching path-prefix."""
    pm = _project_map(cfg)
    if not pm:
        return None
    wd_s = str(wd)
    best: Optional[tuple[int, str]] = None
    for raw_path, project in pm.items():
        cand = _resolve_path(str(raw_path))
        if wd_s == cand:
            return str(project)
        prefix = cand.rstrip("/") + "/"
        if wd_s.startswith(prefix) and (best is None or len(cand) > best[0]):
            best = (len(cand), str(project))
    return best[1] if best else None


# ── layer 3: marker file (.atelier-project) ──────────────────────────────────


def _read_marker(wd: Path) -> Optional[str]:
    """Walk up from `wd` reading the first non-empty line of a
    `.atelier-project` file. Stops at the git root (a dir containing
    `.git`) or the filesystem root — the project boundary."""
    d = wd
    while True:
        marker = d / _MARKER
        if marker.is_file():
            try:
                for line in marker.read_text(encoding="utf-8").splitlines():
                    s = line.strip()
                    if s:
                        return s
            except OSError:                  # pragma: no cover
                return None
            return None
        if (d / ".git").exists():            # at git root → don't cross it
            return None
        if d.parent == d:                    # filesystem root
            return None
        d = d.parent


# ── layer 5: durable git-root identity (linked worktrees) ────────────────────


def _git_root_main(wd: Path) -> Optional[Path]:
    """If `wd` is inside a *linked* git worktree, return the MAIN repo root
    (the toplevel of the primary worktree). Returns None for a primary repo
    (where `.git` is a directory — basename already gives a stable slug) or a
    non-repo dir.

    A linked worktree's `.git` is a *file* pointing at the main repo:
    ``gitdir: /…/<main>/.git/worktrees/<name>``. The main root is the parent
    of that ``.git`` component. This is local-only — no remote is consulted."""
    d = wd
    while True:
        dot = d / ".git"
        if dot.is_file():
            try:
                txt = dot.read_text(encoding="utf-8").strip()
            except OSError:                      # pragma: no cover
                return None
            if not txt.startswith("gitdir:"):    # pragma: no cover
                return None
            gitdir = Path(txt[len("gitdir:"):].strip())
            if not gitdir.is_absolute():         # defensive: relative pointer
                gitdir = (d / gitdir)
            try:
                gitdir = gitdir.resolve()
            except (OSError, RuntimeError):      # pragma: no cover
                pass
            parts = gitdir.parts
            if ".git" not in parts:              # pragma: no cover
                return None
            # the ".git" component in gitdir (e.g. /…/main/.git/worktrees/name);
            # the main root is everything before it.
            idx = len(parts) - 1 - parts[::-1].index(".git")
            return Path(*parts[:idx]) if idx > 0 else None
        if dot.is_dir():                         # primary repo → basename layer
            return None
        if d.parent == d:                        # filesystem root
            return None
        d = d.parent


# ── the accessor ─────────────────────────────────────────────────────────────


def resolve_project(working_dir: Optional[str], *,
                    explicit: Optional[str] = None,
                    cfg: Optional[_config.Config] = None,
                    need_known: bool = True) -> ProjectResolution:
    """Resolve a session's project slug via the layered chain. `cfg` is
    loaded lazily when omitted; pass it to avoid a redundant config read.

    `need_known=False` skips the `known` probe and reports `known=False`. The
    slug layers are pure config/filesystem lookups, but `_is_known` is a DB
    query that falls back to scanning EVERY accepted node when the project has
    no learnings yet — seconds per call on a real vault. A caller that only
    wants the slug (absorb deriving a memory's project) must not pay that, and
    must not be tempted to re-derive the slug itself to avoid it: rolling a
    private derivation is the divergence this module exists to prevent."""
    if cfg is None:
        try:
            cfg = _config.load()
        except Exception:                    # pragma: no cover - no config yet
            cfg = None
    vault = _vault_root(cfg)

    def finalize(slug: Optional[str], source: str) -> ProjectResolution:
        return ProjectResolution(
            slug=slug, source=source,
            known=_is_known(vault, slug) if need_known else False)

    # 1. explicit hint always wins.
    if explicit:
        return finalize(explicit, "explicit")

    if not working_dir:
        return ProjectResolution(slug=None, source="none", known=False)

    wd = Path(working_dir).expanduser()
    try:
        wd = wd.resolve()
    except (OSError, RuntimeError):          # pragma: no cover
        pass

    # 2. config project_map.
    mapped = _match_project_map(cfg, wd)
    if mapped:
        return finalize(mapped, "config-map")

    # 3. marker file.
    marked = _read_marker(wd)
    if marked:
        return finalize(marked, "marker")

    # 4. vault-self (dogfooding inside the vault).
    if vault is not None:
        try:
            wd.relative_to(Path(vault).expanduser().resolve())
            return finalize(SELF_SLUG, "vault-self")
        except (ValueError, RuntimeError):
            pass

    # 5. durable git-root identity — a linked worktree shares the main repo's
    #    slug (honouring the main root's own marker, then its basename).
    main_root = _git_root_main(wd)
    if main_root is not None:
        slug = _read_marker(main_root) or (main_root.name or None)
        if slug:
            return finalize(slug, "git-root")

    # 6. basename fallback (the local default).
    name = wd.name
    if name:
        return finalize(name, "basename")
    return ProjectResolution(slug=None, source="none", known=False)
