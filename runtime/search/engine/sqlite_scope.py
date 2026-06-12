"""SQLite scope → SQL translation (RFC 0003).

`scope_where` lives here, NOT in `types.py`, on purpose: `types.py` is the
backend-free vocabulary ("nothing here knows about SQLite") shared by every
backend, while this helper emits SQLite `AND …` clauses against the `pages`
table's columns. The SQLite-backed modes (`FtsLexical`, `VecSemantic`) share it
so their scope filtering can never diverge; a pgvector backend would ship its
own equivalent and never import this.
"""
from __future__ import annotations

from .types import Scope


def scope_where(scope: Scope, alias: str = "p") -> tuple[list[str], list]:
    """SQL `AND …` clauses + bound params for a `Scope`, over the given `pages`
    alias. Add a new scope dimension here once, not per mode. `provenance` /
    `sensitivity` read the generated columns of the same name (RFC 0003).

    `alias` is a caller-supplied SQL identifier (always a literal like "p" at the
    call sites), never user input — so its interpolation is safe.
    """
    clauses: list[str] = []
    params: list = []
    if scope.space:
        clauses.append(f"AND {alias}.space = ?")
        params.append(scope.space)
    if scope.page_types:
        ph = ",".join("?" * len(scope.page_types))
        clauses.append(f"AND {alias}.page_type IN ({ph})")
        params.extend(scope.page_types)
    if scope.provenance:
        clauses.append(f"AND {alias}.provenance = ?")
        params.append(scope.provenance)
    if scope.sensitivity:
        clauses.append(f"AND {alias}.sensitivity = ?")
        params.append(scope.sensitivity)
    return clauses, params
