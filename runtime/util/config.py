"""Load ~/.atelier/config.yaml and resolve env-var interpolation."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

CONFIG_PATH = Path.home() / ".atelier" / "config.yaml"
CACHE_DIR   = Path.home() / ".atelier" / "cache"
DB_PATH     = CACHE_DIR / "atelier.db"
VOICES_DIR  = Path.home() / ".atelier" / "voices"
SECRETS_ENV = Path.home() / ".atelier" / "secrets" / ".env"

_ENV_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


@dataclass
class SpaceConfig:
    name: str
    local: Path
    remote_type: Optional[str] = None
    remote_url: Optional[str] = None
    remote_branch: Optional[str] = None
    assets: Dict[str, Any] = field(default_factory=dict)
    role: Optional[str] = None


@dataclass
class SubtreeConfig:
    """Writer-role binding for a path inside the single vault.

    Subtrees describe *who* may write to a given path under the vault
    root. The engine uses this to pick the role lock for each write
    operation. Read tools do not consult subtrees.
    """
    path: str             # relative to vault.local (e.g. "wiki", "learnings/candidates")
    writer: str           # WriterRole value: "wiki-write" | "learnings-write" | "captor-write" | "curator-write" | "human-only" (legacy: librarian-write/builder-write)
    append_only: bool = False


@dataclass
class LoggingConfig:
    """Logging sink policy (`logging:` block). Consumed by util.logging.

    `file` defaults (when None) to `~/.atelier/logs/atelier.log`, resolved in
    util.logging from CACHE_DIR. Env `ATELIER_LOG_FILE` / `ATELIER_LOG_LEVEL`
    override at runtime."""
    file: Optional[str] = None
    level: str = "info"               # debug | info | warn | error
    console: bool = True              # stderr echo when interactive (TTY)


@dataclass
class AutoSyncConfig:
    """Background auto-commit/push policy for the vault (`vault.auto_commit`).

    Lives in the `vault:` block because it commits/pushes exactly that vault.
    Disabled by default — the user opts in per-machine. The poller and the
    git primitives read these values; nothing is hard-coded in code."""
    enabled: bool = False
    interval_seconds: int = 30
    push: bool = True
    on_conflict: str = "surface"          # surface | commit-only
    require_stable: bool = True           # commit only if status is unchanged for 2 ticks
    message_prefix: str = "chore(vault):"


@dataclass
class VaultConfig:
    local: Path
    remote_type: Optional[str] = None
    remote_url: Optional[str] = None
    remote_branch: Optional[str] = None
    assets: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Config:
    spaces: Dict[str, SpaceConfig]
    raw: Dict[str, Any]
    vault: Optional[VaultConfig] = None
    subtrees: Dict[str, SubtreeConfig] = field(default_factory=dict)
    auto_sync: AutoSyncConfig = field(default_factory=AutoSyncConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    def space(self, name: str) -> SpaceConfig:
        if name not in self.spaces:
            raise KeyError(f"unknown space: {name!r}; known: {list(self.spaces)}")
        return self.spaces[name]

    def space_by_role(self, role: str) -> SpaceConfig:
        """Resolve a space by its declared role (e.g. 'librarian-territory').

        The engine references roles, not space names — adopters choose their own
        space names in config.yaml. NOTE (RFC 0001): the role *strings*
        (`librarian-territory` / `builder-territory`) are legacy space-binding
        keys, NOT agent personas — the librarian/builder agents were retired.
        They survive only as adopter-config role identifiers; in the single-vault
        model both bind to the one vault root.
        """
        matches = [s for s in self.spaces.values() if s.role == role]
        if not matches:
            raise KeyError(
                f"no space configured with role={role!r}. "
                f"Add `role: {role}` to one space in ~/.atelier/config.yaml."
            )
        if len(matches) > 1:
            names = [s.name for s in matches]
            raise ValueError(
                f"multiple spaces claim role={role!r}: {names}. "
                "Each role must be unique per role-name."
            )
        return matches[0]


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        def repl(m: re.Match[str]) -> str:
            return os.environ.get(m.group(1), "")
        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), val)


def load(path: Optional[Path] = None) -> Config:
    if path is None:
        path = CONFIG_PATH
    # Re-read SECRETS_ENV through the module to honor monkeypatching in tests.
    import sys as _sys
    _mod = _sys.modules[__name__]
    _load_dotenv(getattr(_mod, "SECRETS_ENV"))
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run scripts/setup or copy config/example.config.yaml."
        )
    data = _expand(yaml.safe_load(path.read_text()))

    vault: Optional[VaultConfig] = None
    if data.get("vault"):
        vd = data["vault"]
        remote = vd.get("remote") or {}
        vault = VaultConfig(
            local=Path(vd.get("local", "")).expanduser(),
            remote_type=remote.get("type"),
            remote_url=remote.get("url"),
            remote_branch=remote.get("branch"),
            assets=vd.get("assets") or {},
        )

    subtrees: Dict[str, SubtreeConfig] = {}
    for path_key, sd in (data.get("subtrees") or {}).items():
        subtrees[path_key] = SubtreeConfig(
            path=path_key,
            writer=sd.get("writer", "human-only"),
            append_only=bool(sd.get("append_only", False)),
        )

    spaces: Dict[str, SpaceConfig] = {}
    raw_spaces = data.get("spaces") or {}

    if raw_spaces:
        # Legacy path: explicit spaces dict.
        for name, sd in raw_spaces.items():
            remote = sd.get("remote") or {}
            local = Path(sd.get("local", "")).expanduser()
            spaces[name] = SpaceConfig(
                name=name,
                local=local,
                remote_type=remote.get("type"),
                remote_url=remote.get("url"),
                remote_branch=remote.get("branch"),
                assets=sd.get("assets") or {},
                role=sd.get("role"),
            )
        if vault is not None:
            # Both blocks present is ambiguous — refuse.
            raise ValueError(
                f"{path}: `spaces:` and `vault:` are both set. Use one or "
                "the other (v0.2 prefers `vault:` + `subtrees:`)."
            )
    elif vault is not None:
        # Single-vault path: synthesize two pseudo-spaces so existing
        # callers (cfg.space_by_role) keep working. Both point at the
        # vault root; writes target subtrees inside.
        spaces["vault-librarian"] = SpaceConfig(
            name="vault-librarian",
            local=vault.local,
            remote_type=vault.remote_type,
            remote_url=vault.remote_url,
            remote_branch=vault.remote_branch,
            assets=vault.assets,
            role="librarian-territory",
        )
        spaces["vault-builder"] = SpaceConfig(
            name="vault-builder",
            local=vault.local,
            remote_type=vault.remote_type,
            remote_url=vault.remote_url,
            remote_branch=vault.remote_branch,
            assets=vault.assets,
            role="builder-territory",
        )

    # Auto-sync is a property of the vault it commits, so it lives in the
    # `vault:` block (`vault.auto_commit`).
    ac = (data.get("vault") or {}).get("auto_commit") or {}
    defaults = AutoSyncConfig()
    auto_sync = AutoSyncConfig(
        enabled=bool(ac.get("enabled", defaults.enabled)),
        interval_seconds=int(ac.get("interval_seconds", defaults.interval_seconds)),
        push=bool(ac.get("push", defaults.push)),
        on_conflict=ac.get("on_conflict", defaults.on_conflict),
        require_stable=bool(ac.get("require_stable", defaults.require_stable)),
        message_prefix=ac.get("message_prefix", defaults.message_prefix),
    )

    lg = data.get("logging") or {}
    ldefaults = LoggingConfig()
    level = lg.get("level", ldefaults.level)
    _valid_levels = {"debug", "info", "warn", "error"}
    if level not in _valid_levels:
        raise ValueError(
            f"{path}: logging.level must be one of {sorted(_valid_levels)}; "
            f"got {level!r}.")
    logging_cfg = LoggingConfig(
        file=lg.get("file", ldefaults.file),
        level=level,
        console=bool(lg.get("console", ldefaults.console)),
    )

    cfg = Config(spaces=spaces, raw=data, vault=vault, subtrees=subtrees,
                 auto_sync=auto_sync, logging=logging_cfg)
    _validate_strict(cfg, path)
    return cfg


# ── Strict-mode validation ────────────────────────────────────────────────────
#
# atelier refuses to start if config still contains placeholder values.
# A "placeholder" is any string with angle-brackets like `<...>` or the
# obvious filler tokens (`your-`, `path/to/your`, `REQUIRED`). Engine has
# no user-specific defaults — every binding must come from this file.

_PLACEHOLDER_TOKENS = ("<", ">", "REQUIRED", "your-", "path/to/your")


def _looks_like_placeholder(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return any(tok in value for tok in _PLACEHOLDER_TOKENS)


_LOOPBACK_BINDS = ("127.0.0.1", "localhost", "::1")


# Writer identifiers, keyed by subtree (RFC 0001 retired the librarian/builder
# agent personas; the legacy names are still accepted so existing per-machine
# configs keep validating).
_VALID_WRITERS = {"human-only", "wiki-write", "learnings-write",
                  "captor-write", "curator-write",
                  "librarian-write", "builder-write"}   # legacy aliases


def _validate_strict(cfg: "Config", path: Path) -> None:
    problems: list[str] = []

    if cfg.vault is not None:
        if _looks_like_placeholder(str(cfg.vault.local)):
            problems.append(
                f"vault.local is still a placeholder: {cfg.vault.local!r}"
            )
        if cfg.vault.remote_url and _looks_like_placeholder(cfg.vault.remote_url):
            problems.append(
                f"vault.remote.url is still a placeholder: {cfg.vault.remote_url!r}"
            )
        for st_path, st in cfg.subtrees.items():
            if st.writer not in _VALID_WRITERS:
                problems.append(
                    f"subtree {st_path!r}: writer={st.writer!r} not in "
                    f"{sorted(_VALID_WRITERS)}"
                )

    for name, sp in cfg.spaces.items():
        if not sp.role:
            problems.append(f"space {name!r}: missing required `role:` "
                            "(e.g. librarian-territory)")
        if _looks_like_placeholder(str(sp.local)):
            problems.append(f"space {name!r}.local is still a placeholder: {sp.local!r}")
        if sp.remote_url and _looks_like_placeholder(sp.remote_url):
            problems.append(f"space {name!r}.remote.url is still a placeholder: "
                            f"{sp.remote_url!r}")

    svc = (cfg.raw.get("service") or {})
    if svc.get("enabled"):
        if _looks_like_placeholder(str(svc.get("allowed_user", ""))):
            problems.append("service.allowed_user is still a placeholder")
        http = svc.get("mcp_http") or {}
        if http.get("enabled"):
            bind = http.get("bind", "127.0.0.1")
            if bind not in _LOOPBACK_BINDS:
                problems.append(
                    f"service.mcp_http.bind must be loopback "
                    f"({list(_LOOPBACK_BINDS)}); got {bind!r}"
                )
            env_var = http.get("token_env", "ATELIER_MCP_HTTP_TOKEN")
            if _looks_like_placeholder(env_var):
                problems.append(
                    f"service.mcp_http.token_env is still a placeholder: {env_var!r}"
                )

    if problems:
        lines = "\n  - ".join([""] + problems)
        raise ValueError(
            f"atelier config at {path} has unresolved placeholders. "
            f"Edit it and try again:{lines}"
        )


def ensure_cache_dir() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR
