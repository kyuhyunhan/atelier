"""Graph-mode search: BFS over the links table from seed pages."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Neighbor:
    slug: str
    distance: int
    via: str  # 'inbound' | 'outbound'


def neighborhood(
    conn: sqlite3.Connection,
    seed_slug: str,
    depth: int = 2,
) -> List[Neighbor]:
    """BFS both directions up to `depth` hops."""
    seed = conn.execute("SELECT id FROM pages WHERE slug=?", (seed_slug,)).fetchone()
    if not seed:
        return []
    seed_id = seed["id"]

    seen: dict[int, tuple[int, str]] = {seed_id: (0, "self")}
    frontier = [seed_id]
    for d in range(1, depth + 1):
        next_frontier: List[int] = []
        if not frontier:
            break
        placeholders = ",".join("?" * len(frontier))
        # outbound
        for r in conn.execute(
            f"SELECT DISTINCT to_page_id FROM links "
            f"WHERE from_page IN ({placeholders}) AND to_page_id IS NOT NULL",
            frontier,
        ):
            nid = r["to_page_id"]
            if nid not in seen:
                seen[nid] = (d, "outbound")
                next_frontier.append(nid)
        # inbound
        for r in conn.execute(
            f"SELECT DISTINCT from_page FROM links "
            f"WHERE to_page_id IN ({placeholders})",
            frontier,
        ):
            nid = r["from_page"]
            if nid not in seen:
                seen[nid] = (d, "inbound")
                next_frontier.append(nid)
        frontier = next_frontier

    ids = list(seen.keys())
    rows = {
        r["id"]: r["slug"]
        for r in conn.execute(
            f"SELECT id, slug FROM pages WHERE id IN ({','.join('?'*len(ids))})", ids
        )
    }
    out: List[Neighbor] = []
    for pid, (dist, via) in seen.items():
        if pid == seed_id:
            continue
        out.append(Neighbor(slug=rows[pid], distance=dist, via=via))
    out.sort(key=lambda n: (n.distance, n.slug))
    return out


def inbound(conn: sqlite3.Connection, slug: str) -> List[str]:
    p = conn.execute("SELECT id FROM pages WHERE slug=?", (slug,)).fetchone()
    if not p:
        return []
    return [r["slug"] for r in conn.execute(
        "SELECT DISTINCT pp.slug FROM links l JOIN pages pp ON pp.id = l.from_page "
        "WHERE l.to_page_id=? ORDER BY pp.slug", (p["id"],),
    )]


def outbound(conn: sqlite3.Connection, slug: str) -> List[str]:
    p = conn.execute("SELECT id FROM pages WHERE slug=?", (slug,)).fetchone()
    if not p:
        return []
    return [r["slug"] for r in conn.execute(
        "SELECT DISTINCT pp.slug FROM links l JOIN pages pp ON pp.id = l.to_page_id "
        "WHERE l.from_page=? ORDER BY pp.slug", (p["id"],),
    )]
