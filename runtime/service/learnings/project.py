"""Single source of truth for working-dir → project-slug resolution.

Three call paths must agree on "what project is this session":

- **capture** writes the `project_hint` tag (and the by-project mirror),
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
  5. basename    — ``Path(working_dir).name`` (the local default)

`known` reports whether ``learnings/accepted/by-project/<slug>/`` exists,
so callers can warn loudly when a session's captures won't be recalled
instead of failing silently.
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
    known: bool              # learnings/accepted/by-project/<slug>/ exists


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
    if not vault or not slug:
        return False
    return (Path(vault) / "learnings" / "accepted" / "by-project" / slug).is_dir()


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


# ── the accessor ─────────────────────────────────────────────────────────────


def resolve_project(working_dir: Optional[str], *,
                    explicit: Optional[str] = None,
                    cfg: Optional[_config.Config] = None) -> ProjectResolution:
    """Resolve a session's project slug via the layered chain. `cfg` is
    loaded lazily when omitted; pass it to avoid a redundant config read."""
    if cfg is None:
        try:
            cfg = _config.load()
        except Exception:                    # pragma: no cover - no config yet
            cfg = None
    vault = _vault_root(cfg)

    def finalize(slug: Optional[str], source: str) -> ProjectResolution:
        return ProjectResolution(slug=slug, source=source,
                                 known=_is_known(vault, slug))

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

    # 5. basename fallback (the local default).
    name = wd.name
    if name:
        return finalize(name, "basename")
    return ProjectResolution(slug=None, source="none", known=False)
