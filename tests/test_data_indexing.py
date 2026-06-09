"""RFC 0002 P1b — structured files (*.yaml/*.yml/*.json) become searchable.

Closes the non-markdown blind spot (§6): a yaml/json file is walked, flattened
into chunk text, classified as a `data` page, and findable by content — while
`secrets/**` and `*.local.*` stay excluded (privacy). The doctor's drift check
must walk the same set the indexer does, or every data page reads as drift.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

from runtime.util import fs


# ── walk_indexable: coverage + exclusions ───────────────────────────────────

def test_walk_indexable_includes_structured_and_excludes_private(tmp_path: Path):
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "note.md").write_text("# n\n")
    (tmp_path / "data.yaml").write_text("a: 1\n")
    (tmp_path / "data.yml").write_text("b: 2\n")
    (tmp_path / "data.json").write_text('{"c": 3}\n')
    # private — must be excluded
    (tmp_path / "config.local.yaml").write_text("token: x\n")
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "keys.yaml").write_text("k: v\n")
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "app.json").write_text("{}\n")

    found = {fs.slug_for(tmp_path, p) for p in fs.walk_indexable(tmp_path)}
    assert "wiki/note.md" in found
    assert {"data.yaml", "data.yml", "data.json"} <= found
    assert "config.local.yaml" not in found       # *.local.* excluded
    assert "secrets/keys.yaml" not in found        # secrets/** excluded
    assert ".obsidian/app.json" not in found       # hidden dir skipped


def test_walk_indexable_excludes_tooling_artifacts(tmp_path: Path):
    """Build/tooling structured files are noise, not knowledge — excluded. But a
    markdown file is never tooling, and genuine content yaml is kept."""
    (tmp_path / "package.json").write_text('{"name": "x"}\n')
    (tmp_path / "package-lock.json").write_text('{"lockfileVersion": 3}\n')
    (tmp_path / "tsconfig.base.json").write_text('{"compilerOptions": {}}\n')
    (tmp_path / ".eslintrc.json").write_text('{"rules": {}}\n')
    (tmp_path / "contract.yaml").write_text("name: real-content\n")
    (tmp_path / "notes.md").write_text("# real\n")

    found = {fs.slug_for(tmp_path, p) for p in fs.walk_indexable(tmp_path)}
    assert "contract.yaml" in found and "notes.md" in found
    for tooling in ("package.json", "package-lock.json", "tsconfig.base.json",
                    ".eslintrc.json"):
        assert tooling not in found


# ── parse_data_file: flatten to searchable text ─────────────────────────────

def test_parse_data_file_flattens_yaml_values(tmp_path: Path):
    from runtime.index import parse as _parse
    p = tmp_path / "c.yaml"
    p.write_text("name: alpha-contract\nholds:\n  - eviction-policy\n  - warmup\n")
    parsed = _parse.parse_data_file(p)
    text = "\n".join(c.text for c in parsed.chunks).lower()
    assert "alpha-contract" in text
    assert "eviction-policy" in text
    assert "warmup" in text


def test_parse_data_file_survives_malformed(tmp_path: Path):
    from runtime.index import parse as _parse
    p = tmp_path / "bad.yaml"
    p.write_text("a: [unterminated\n: : :\n")
    parsed = _parse.parse_data_file(p)          # must not raise
    assert parsed.chunks                          # falls back to raw text


# ── e2e: indexed, searchable, classified, no drift ──────────────────────────

def test_yaml_is_searchable_by_content_and_classified_data(vault_env: Dict):
    vault = vault_env["vault"]
    (vault / "wiki").mkdir(parents=True, exist_ok=True)
    (vault / "wiki" / "contract.yaml").write_text(
        "name: caching-contract\nguarantees:\n  - bounded-staleness\n")
    from runtime.service import api
    api.reindex(full=True)

    hits = api.search("bounded-staleness", space=None)
    assert any("contract.yaml" in h["slug"] for h in hits)
    assert any(h["page_type"] == "data" for h in hits if "contract.yaml" in h["slug"])


def test_secret_yaml_not_indexed(vault_env: Dict):
    vault = vault_env["vault"]
    (vault / "secrets").mkdir(parents=True, exist_ok=True)
    (vault / "secrets" / "creds.yaml").write_text("api_key: SUPERSECRETVALUE\n")
    from runtime.service import api
    api.reindex(full=True)
    hits = api.search("SUPERSECRETVALUE", space=None)
    assert not any("creds.yaml" in h["slug"] for h in hits)


def test_doctor_drift_clean_after_yaml_indexing(vault_env: Dict):
    vault = vault_env["vault"]
    (vault / "wiki").mkdir(parents=True, exist_ok=True)
    (vault / "wiki" / "spec.yaml").write_text("k: v\n")
    from runtime.service import api
    api.reindex(full=True)

    from runtime.doctor import diagnostics
    from runtime.util import config as _config
    d = diagnostics.D2_filesystem_drift(_config.load())
    assert d.severity == "OK", d.message
