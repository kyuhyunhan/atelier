"""All schema YAML files must parse and satisfy minimal structural expectations."""
from __future__ import annotations

from pathlib import Path

import yaml

SCHEMA = Path(__file__).resolve().parents[1] / "schema" / "data"


def test_base_yaml_loads_and_versions_5():
    data = yaml.safe_load((SCHEMA / "base.yaml").read_text())
    assert data["version"] == 5
    assert "schema_version" in data["fields"]
    # v5 is current; v4 accepted during the flat-facet migration (RFC 0001).
    assert data["fields"]["schema_version"]["enum"] == [4, 5]


def test_librarian_overlay_has_5_wiki_page_types():
    data = yaml.safe_load((SCHEMA / "librarian.overlay.yaml").read_text())
    assert data["agent"] == "librarian"
    assert "gorae" in data["spaces"]
    wiki_types = {"digest", "source", "entity", "theme", "synthesis"}
    assert wiki_types <= set(data["page_types"])


def test_builder_overlay_has_workshop_space():
    data = yaml.safe_load((SCHEMA / "builder.overlay.yaml").read_text())
    assert data["agent"] == "builder"
    assert "workshop" in data["spaces"]
    assert "product_readme" in data["page_types"]


def test_linking_yaml_registers_both_schemes():
    data = yaml.safe_load((SCHEMA / "linking.yaml").read_text())
    assert "gorae" in data["schemes"]
    assert "workshop" in data["schemes"]
    assert data["backward_compat"]["v3_implicit_space"] == "gorae"


def test_lint_yaml_defines_l1_through_l7():
    data = yaml.safe_load((SCHEMA / "lint.yaml").read_text())
    ids = set(data["rules"])
    assert ids == {"L1", "L2", "L3", "L4", "L5", "L6", "L7"}
    assert data["rules"]["L1"]["severity"] == "FAIL"
    assert data["rules"]["L3"]["fix"] is not None


def test_learnings_overlay_has_lifecycle_page_types():
    data = yaml.safe_load((SCHEMA / "learnings.overlay.yaml").read_text())
    assert data["subtree"] == "learnings"
    assert "captor" in data["agents"]
    assert "curator" in data["agents"]
    expected = {"learning_candidate", "learning_accepted", "learning_archived"}
    assert expected <= set(data["page_types"])
    # candidate must be append_only and writer=captor
    cand = data["page_types"]["learning_candidate"]
    assert cand["writer"] == "captor"
    assert cand["append_only"] is True
    # acceptance criteria template ships embedded for first-run vault seed
    assert "acceptance_criteria_template" in data
    assert "must" in data["acceptance_criteria_template"]


def test_sql_migration_present():
    sql_dir = Path(__file__).resolve().parents[1] / "schema" / "db" / "sql"
    assert (sql_dir / "0001_initial.sql").exists()
    sql = (sql_dir / "0001_initial.sql").read_text()
    for table in ("pages", "chunks", "chunks_fts", "links", "entities", "meta"):
        assert table in sql, f"missing table {table}"
