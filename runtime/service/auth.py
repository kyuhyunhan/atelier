"""Authentication / token validation. Placeholder in v0.1.

The shape exists so v0.2's MCP/HTTPS surfaces can plug a real verifier in
without disturbing callers.
"""
from __future__ import annotations

import os
from typing import Optional

from . import claims


def authenticate(token: Optional[str] = None) -> claims.CallContext:
    """Resolve a token into a CallContext. v0.1: returns local-cli for any input."""
    if token is None:
        return claims.local_cli_context()
    # Future: validate token against ATELIER_*_TOKEN env vars or a key store.
    expected = os.environ.get("ATELIER_API_TOKEN")
    if expected and token == expected:
        return claims.CallContext(caller="api-client",
                                  claims=frozenset({claims.Claim.LIBRARIAN_WRITE,
                                                    claims.Claim.BUILDER_WRITE}))
    return claims.local_cli_context()
