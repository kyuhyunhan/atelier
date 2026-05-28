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
class Config:
    spaces: Dict[str, SpaceConfig]
    raw: Dict[str, Any]

    def space(self, name: str) -> SpaceConfig:
        if name not in self.spaces:
            raise KeyError(f"unknown space: {name!r}; known: {list(self.spaces)}")
        return self.spaces[name]

    def space_by_role(self, role: str) -> SpaceConfig:
        """Resolve a space by its declared role (e.g. 'librarian-territory').

        The engine references roles, not space names — adopters choose
        their own space names in config.yaml.
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

    spaces: Dict[str, SpaceConfig] = {}
    for name, sd in (data.get("spaces") or {}).items():
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

    cfg = Config(spaces=spaces, raw=data)
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


def _validate_strict(cfg: "Config", path: Path) -> None:
    problems: list[str] = []
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
