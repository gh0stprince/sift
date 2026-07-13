"""Filesystem-safe tests for automatic curation."""
from pathlib import Path

import pytest

from sift.curation import CurationError, apply_curation, plan_curation


def make_vault(tmp_path: Path) -> tuple[Path, Path]:
    vault = tmp_path / "vault"
    meta = vault / "10-system" / "11-meta"
    meta.mkdir(parents=True)
    (meta / "11.01 SCHEMA.md").write_text("schema", encoding="utf-8")
    (meta / "11.02 index.md").write_text("# Index\n", encoding="utf-8")
    (meta / "11.03 log.md").write_text("# Log\n", encoding="utf-8")
    raw = vault / "raw" / "queries"
    raw.mkdir(parents=True)
    (raw / "query.md").write_text(
        '---\ntitle: "Research query"\nsource_query: "what is X"\ningested: 2026-07-13\n---\n\nX is useful.\n',
        encoding="utf-8")
    return vault, raw


def synth(capture):
    return {"title": "X concept", "type": "concept", "body": "X is useful.",
            "links": ["related concept"], "claims": []}


def test_dry_run_does_not_write(tmp_path):
    vault, raw = make_vault(tmp_path)
    before = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
    plans = plan_curation(raw, vault, synth)
    result = apply_curation(plans, vault, dry_run=True)
    assert result["created"] == ["x-concept"]
    assert {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()} == before


def test_apply_is_idempotent_and_preserves_raw(tmp_path):
    vault, raw = make_vault(tmp_path)
    original = (raw / "query.md").read_bytes()
    plans = plan_curation(raw, vault, synth)
    first = apply_curation(plans, vault)
    second = apply_curation(plan_curation(raw, vault, synth), vault)
    assert first["created"] == ["x-concept"]
    assert second["unchanged"] == ["x-concept"]
    assert (raw / "query.md").read_bytes() == original
    assert (vault / "10-system/11-meta/11.02 index.md").read_text(encoding="utf-8").count("x-concept") == 1
    assert (vault / "10-system/11-meta/11.03 log.md").read_text(encoding="utf-8").count("query.md") == 1


def test_missing_vault_contract_fails(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    with pytest.raises(CurationError, match="schema"):
        plan_curation(raw, tmp_path, synth)


def test_existing_page_is_appended_not_overwritten(tmp_path):
    vault, raw = make_vault(tmp_path)
    target = vault / "20-knowledge-tech/21-ai-concepts/x-concept.md"
    target.parent.mkdir(parents=True)
    target.write_text("---\ntitle: X concept\n---\n\nOriginal claim.\n", encoding="utf-8")
    plan = plan_curation(raw, vault, synth)[0]
    assert "Original claim." in plan.content
    assert "X is useful." in plan.content
    assert plan.conflicts
