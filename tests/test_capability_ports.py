"""PR-9/10/12/14: ports of proto-engine capabilities into atelier.

Covers fix_pending, index_regen, clip_image, new_doc.
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any, Dict, Tuple

import pytest
import yaml

from runtime.service.jobs import clip as _clip
from runtime.service.jobs import index_regen as _idx
from runtime.service.jobs import new_doc as _nd
from runtime.service.jobs import pending as _pp


def _write(path: Path, fm: Dict, body: str = "body\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip()
    path.write_text(f"---\n{serialized}\n---\n{body}", encoding="utf-8")


# ── fix_pending (PR-9) ─────────────────────────────────────────────────────


def test_fix_pending_dry_run(atelier_env: Dict) -> None:
    vault = atelier_env["gorae"]
    _write(vault / "raw" / "x.md", {"schema_version": 4, "entry_id": "PENDING"})
    out = _pp.fix_pending(dry_run=True)
    assert out["count"] == 1
    text = (vault / "raw" / "x.md").read_text()
    assert "PENDING" in text  # unchanged


def test_fix_pending_apply(atelier_env: Dict) -> None:
    vault = atelier_env["gorae"]
    _write(vault / "raw" / "x.md", {"schema_version": 4, "entry_id": "PENDING"})
    out = _pp.fix_pending(dry_run=False)
    assert out["count"] == 1
    text = (vault / "raw" / "x.md").read_text()
    assert "PENDING" not in text
    assert out["fixed"][0]["new_entry_id"].count("-") == 4  # UUID shape


# ── index_regen (PR-10) ────────────────────────────────────────────────────


def test_index_regen_creates_file(atelier_env: Dict) -> None:
    vault = atelier_env["gorae"]
    _write(vault / "graph" / "entities" / "foo.md",
           {"schema_version": 4, "entry_id": "abc", "title": "Foo"})
    out = _idx.regen()
    assert out["changed"] is True
    assert out["page_count"] == 1
    text = (vault / "graph" / "index.md").read_text()
    assert "entities (1)" in text
    assert "[[foo]] — Foo" in text


def test_index_regen_idempotent_second_run(atelier_env: Dict) -> None:
    vault = atelier_env["gorae"]
    _write(vault / "wiki" / "themes" / "bar.md",
           {"schema_version": 4, "entry_id": "ab", "title": "Bar"})
    _idx.regen()
    out2 = _idx.regen()
    assert out2["changed"] is False


def test_index_regen_targets_graph_tree_post_rename(atelier_env: Dict) -> None:
    """RFC 0003 GP1: on a renamed vault (graph/, no wiki/), regen must scan AND
    write graph/ — never resurrect the deprecated wiki/ tree (the 1507 bug class:
    a writer whose target misses a rename re-creates the old tree)."""
    vault = atelier_env["gorae"]
    shutil.rmtree(vault / "wiki", ignore_errors=True)
    _write(vault / "graph" / "entities" / "foo.md",
           {"schema_version": 4, "entry_id": "abc", "title": "Foo"})
    out = _idx.regen()
    assert out["page_count"] == 1
    assert (vault / "graph" / "index.md").exists()
    assert not (vault / "wiki").exists(), "must not resurrect the deprecated wiki/ tree"


# ── clip_image (PR-12) ─────────────────────────────────────────────────────


def _fake_fetch(payload: bytes, ct: str) -> Any:
    def go(url: str) -> Tuple[bytes, str]:
        return payload, ct
    return go


def test_clip_image_writes_local_file(atelier_env: Dict) -> None:
    out = _clip.clip_image(
        url="https://example.com/foo.png",
        fetch=_fake_fetch(b"PNGDATA", "image/png"),
    )
    p = Path(out["local"])
    assert p.exists()
    assert p.read_bytes() == b"PNGDATA"
    assert p.suffix == ".png"
    # vault-relative path lives under gorae-resources/
    assert out["rel"].startswith("gorae-resources/")


def test_clip_image_returns_cdn_when_configured(atelier_env: Dict) -> None:
    cfg_path = atelier_env["home"] / "config.yaml"
    data = yaml.safe_load(cfg_path.read_text())
    data["spaces"]["gorae"]["assets"] = {"type": "r2",
                                          "cdn": "https://cdn.example/"}
    cfg_path.write_text(yaml.safe_dump(data))
    out = _clip.clip_image(
        url="https://example.com/bar.jpg",
        fetch=_fake_fetch(b"JPGDATA", "image/jpeg"),
    )
    assert out["cdn"] is not None
    assert out["cdn"].startswith("https://cdn.example/gorae-resources/")


# ── new_doc (PR-14) ────────────────────────────────────────────────────────


def test_new_doc_product(atelier_env: Dict) -> None:
    out = _nd.new_doc(template="product", name="lexio")
    assert Path(out["path"]).exists()
    assert out["path"].endswith("products/lexio/README.md")


def test_new_doc_raw(atelier_env: Dict) -> None:
    out = _nd.new_doc(template="raw", name="2026-05-28-note",
                      fields={"title": "Quick capture"})
    p = Path(out["path"])
    assert p.exists()
    assert "raw/personal/inbox/2026-05-28-note.md" in str(p)
    text = p.read_text()
    assert "Quick capture" in text


def test_new_doc_note(atelier_env: Dict) -> None:
    out = _nd.new_doc(template="note", name="weekly")
    p = Path(out["path"])
    assert "workshop/notes/weekly.md" in str(p) or "notes/weekly.md" in str(p)
    assert p.exists()


def test_new_doc_learning_retired(atelier_env: Dict) -> None:
    # RFC 0005 §7.1: the learning candidate-file scaffold is retired; learnings are
    # born as a Claim via atelier_learning_capture. new_doc redirects, never writes legacy.
    with pytest.raises(ValueError, match="born as a Claim"):
        _nd.new_doc(template="learning", name="manual-1",
                    fields={"project_hint": "lexio"})


def test_new_doc_refuses_collision(atelier_env: Dict) -> None:
    _nd.new_doc(template="product", name="dup")
    with pytest.raises(FileExistsError):
        _nd.new_doc(template="product", name="dup")


def test_new_doc_unknown_template_rejected(atelier_env: Dict) -> None:
    with pytest.raises(ValueError):
        _nd.new_doc(template="bogus", name="x")


# ── MCP dispatch parity ────────────────────────────────────────────────────


def test_all_new_tools_registered() -> None:
    from runtime.service import tools as _tools
    names = {t.name for t in _tools.iter_tools()}
    assert {"atelier_fix_pending", "atelier_index_regen",
            "atelier_clip_image", "atelier_new_doc"} <= names


def test_mcp_dispatch_fix_pending(atelier_env: Dict) -> None:
    from runtime.service import tools as _tools
    vault = atelier_env["gorae"]
    _write(vault / "raw" / "y.md", {"schema_version": 4, "entry_id": "PENDING"})
    async def go() -> Dict:
        return await _tools.invoke("atelier_fix_pending", dry_run=False)
    out = asyncio.run(go())
    assert out["count"] == 1
