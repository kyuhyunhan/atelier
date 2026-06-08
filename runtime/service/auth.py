"""Authentication for atelier engine transports.

Two callers exist in v0.2:

- **local-cli** — the one-shot CLI; full trust, all claims implicit.
- **mcp-http** — a Claude Code (or other agent) connecting via the
  local-bound HTTP MCP transport; presents a static bearer token from
  `~/.atelier/secrets/.env` (`ATELIER_MCP_HTTP_TOKEN`). Loopback-only.

`Session` is the per-call shape every MCP tool wrapper threads through;
fields are populated by the transport adapter, not the engine. `agent_kind`
is for logs/telemetry only — authorization is by claim, not by agent.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from . import claims as _claims


# Default claim set granted to a bearer-authenticated MCP-HTTP caller.
# Read-only tools never check claims. Write tools check the specific
# claim they need; the bearer-authenticated session carries all of these
# in v0.2 (single owner-user).
_BEARER_DEFAULT_CLAIMS = frozenset({
    _claims.Claim.WIKI_WRITE,
    _claims.Claim.LEARNINGS_WRITE,
    _claims.Claim.CAPTOR_WRITE,
    _claims.Claim.CURATOR_WRITE,
    _claims.Claim.PROMOTE_APPLY,
    _claims.Claim.MOBILE_CLAIM,
    _claims.Claim.DOCTOR_REMEDIATE,
})


@dataclass(frozen=True)
class Session:
    """Per-call context. Built by transport adapters; opaque to api.py."""
    agent_kind: str = "claude-code"        # "claude-code" | "hermes" | "unknown"
    transport: str = "local-cli"           # "local-cli" | "mcp-stdio" | "mcp-http"
    session_id: Optional[str] = None
    working_dir: Optional[str] = None
    caller: str = "local-cli"
    claims: frozenset[_claims.Claim] = field(default_factory=frozenset)

    def to_call_context(self) -> _claims.CallContext:
        """Bridge to the v0.1 CallContext used by existing api.py code."""
        return _claims.CallContext(caller=self.caller, claims=self.claims)


def local_cli_session() -> Session:
    """Full-trust session for in-process CLI calls."""
    return Session(
        agent_kind="local",
        transport="local-cli",
        caller="local-cli",
        claims=frozenset(_claims.Claim),
    )


def authenticate_bearer(
    token: Optional[str],
    *,
    transport: str,
    agent_kind: str = "claude-code",
    session_id: Optional[str] = None,
    working_dir: Optional[str] = None,
    env_var: str = "ATELIER_MCP_HTTP_TOKEN",
) -> Session:
    """Validate a bearer token. Raises PermissionError on mismatch.

    The expected token is read from environment (loaded from
    `~/.atelier/secrets/.env` by util.config). An empty/missing
    environment value with an empty input token is rejected — the engine
    refuses to accept anonymous callers on remote transports.
    """
    expected = os.environ.get(env_var, "")
    if not expected:
        raise PermissionError(
            f"{env_var} is unset in ~/.atelier/secrets/.env; remote "
            "transport is disabled until a token is configured."
        )
    if not token or token != expected:
        raise PermissionError("invalid bearer token")
    return Session(
        agent_kind=agent_kind,
        transport=transport,
        session_id=session_id,
        working_dir=working_dir,
        caller=f"{transport}:{agent_kind}",
        claims=_BEARER_DEFAULT_CLAIMS,
    )


# ── v0.1 compatibility shim (still called by runtime/service/api.py) ────────


def authenticate(token: Optional[str] = None) -> _claims.CallContext:
    """Legacy entry from api.py. v0.2 keeps it returning local-cli for
    the sync path; MCP-bound calls use authenticate_bearer + Session and
    bypass this function entirely."""
    if token is None:
        return _claims.local_cli_context()
    expected = os.environ.get("ATELIER_API_TOKEN")
    if expected and token == expected:
        return _claims.CallContext(
            caller="api-client",
            claims=frozenset({_claims.Claim.WIKI_WRITE,
                              _claims.Claim.LEARNINGS_WRITE}),
        )
    return _claims.local_cli_context()
