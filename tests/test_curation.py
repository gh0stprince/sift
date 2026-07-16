"""Filesystem-safe tests for automatic curation."""
from pathlib import Path

from click.testing import CliRunner
import pytest

from sift.cli import main
from sift.curation import CurationError, _normalize_markdown, apply_curation, plan_curation


def make_vault(tmp_path: Path) -> tuple[Path, Path]:
    vault = tmp_path / "vault"
    meta = vault / "10-system" / "11-meta"
    meta.mkdir(parents=True)
    (meta / "11.01 SCHEMA.md").write_text("Frontmatter workflow schema", encoding="utf-8")
    (meta / "11.02 index.md").write_text("# Index\n", encoding="utf-8")
    (meta / "11.03 log.md").write_text("# Log\n", encoding="utf-8")
    raw = vault / "raw" / "queries"
    raw.mkdir(parents=True)
    (raw / "query.md").write_text(
        '---\ntitle: "Research query"\nsource_query: "what is X"\ningested: 2026-07-13\n---\n\nX is useful.\n',
        encoding="utf-8")
    return vault, raw


def synth(_capture):
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
    wrapped_plan = plan_curation(raw, vault, lambda _capture: {
        "title": "X concept", "type": "concept", "body": "Wrapped claim\ncontinues here.",
        "links": [], "claims": []})[0]
    assert "Wrapped claim continues here." in wrapped_plan.content


def test_existing_page_append_preserves_complete_new_provenance(tmp_path):
    vault, _raw = make_vault(tmp_path)
    legacy_raw = vault / "80-raw" / "82-queries" / "legacy.md"
    legacy_raw.parent.mkdir(parents=True)
    legacy_raw.write_text(
        "---\ntitle: Legacy capture\ntype: raw-source\n"
        "source_query: legacy research\ningested: 2026-07-14\n---\n\n"
        "Source: https://example.com/research\n\nNew finding.\n",
        encoding="utf-8",
    )
    target = vault / "20-knowledge-tech" / "21-ai-concepts" / "x-concept.md"
    target.parent.mkdir(parents=True)
    target.write_text("---\ntitle: X concept\n---\n\nOriginal claim.\n", encoding="utf-8")

    plan = plan_curation(legacy_raw, vault, synth)[0]

    assert "Raw capture: `80-raw/82-queries/legacy.md`" in plan.content
    assert "Source hash: `" + plan.source.digest + "`" in plan.content
    assert "Query: `legacy research`" in plan.content
    assert "Source URL: https://example.com/research" in plan.content


def test_curation_quotes_captured_provenance_scalar(tmp_path):
    """Unexpected captured metadata cannot alter curated YAML structure."""
    vault, raw = make_vault(tmp_path)
    capture = raw / "query.md"
    capture.write_text(
        '---\ntitle: "Research query"\nsource_query: "what is X"\n'
        'ingested: "2026-07-13: draft #1"\nsource:\n'
        '  - "https://example.com/source"\n---\n\nX is useful.\n',
        encoding="utf-8",
    )

    plan = plan_curation(capture, vault, synth)[0]

    assert 'captured: "2026-07-13: draft #1"' in plan.content


def test_file_input_processes_only_selected_capture(tmp_path):
    vault, raw = make_vault(tmp_path)
    sibling = raw / "sibling.md"
    sibling.write_text('---\ntitle: Sibling\n---\n\nSibling body.\n', encoding="utf-8")
    result = CliRunner().invoke(main, ["curate", "--file", str(raw / "query.md"),
                                        "--vault", str(vault), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Preview 1 capture(s)" in result.output
    assert "sibling" not in result.output.lower()


def test_curated_file_is_rejected(tmp_path):
    vault, raw = make_vault(tmp_path)
    curated = vault / "20-knowledge-tech" / "21-ai-concepts" / "already.md"
    curated.parent.mkdir(parents=True)
    curated.write_text("---\ntype: concept\n---\n\nExisting page.\n", encoding="utf-8")
    result = CliRunner().invoke(main, ["curate", "--file", str(curated),
                                        "--vault", str(vault), "--dry-run"])
    assert result.exit_code != 0
    assert "refusing curated page" in result.output
    assert "raw capture" in result.output

    elsewhere = tmp_path / "elsewhere.md"
    elsewhere.write_text("---\ntitle: Elsewhere\n---\n\nExisting page.\n", encoding="utf-8")
    result = CliRunner().invoke(main, ["curate", "--file", str(elsewhere),
                                        "--vault", str(vault), "--dry-run"])
    assert result.exit_code != 0
    assert "not under raw/queries" in result.output


def test_reasoning_and_rendered_sources_are_stripped(tmp_path):
    vault, raw = make_vault(tmp_path)

    capture = raw / "polluted.md"
    capture.write_text(
        "---\ntitle: Folk magic\ntype: raw-source\nsource_query: folk magic\n---\n\n"
        "Thinking...\n1. **Analyze** the prompt.\n2. **Draft** an answer.\n"
        "Final answer:\nFolk magic is a useful concept.\n\n## Sources\n\n- https://example.com\n",
        encoding="utf-8",
    )
    plans = plan_curation(capture, vault, lambda _capture: {
        "title": "Folk magic", "type": "concept",
        "body": capture.read_text(encoding="utf-8").split("---", 2)[-1],
        "links": [], "claims": [],
    })
    content = next(plan.content for plan in plans if plan.slug == "folk-magic")
    assert "Folk magic is a useful concept." in content
    assert "Thinking" not in content
    assert "Analyze" not in content
    assert content.count("## Sources") == 1


def test_missing_source_query_is_visible_conflict(tmp_path):
    vault, raw = make_vault(tmp_path)
    capture = raw / "no-query.md"
    capture.write_text("---\ntitle: No query\ntype: raw-source\n---\n\nBody.\n", encoding="utf-8")
    plan = plan_curation(capture, vault, synth)[0]
    assert "incomplete" in plan.content
    assert any("source_query" in item for item in plan.conflicts)


def test_heuristic_curation_preserves_body_after_horizontal_rule(tmp_path):
    vault, raw = make_vault(tmp_path)
    capture = raw / "horizontal-rule.md"
    capture.write_text(
        "---\ntitle: Divided notes\ntype: raw-source\nsource_query: divided notes\n---\n\n"
        "First section.\n\n---\n\nSecond section must survive.\n",
        encoding="utf-8",
    )

    plan = next(plan for plan in plan_curation(capture, vault)
                if plan.slug == "divided-notes")

    assert "First section." in plan.content
    assert "Second section must survive." in plan.content


def test_duplicate_synthesized_slugs_fail_visibly(tmp_path):
    vault, raw = make_vault(tmp_path)
    (raw / "second.md").write_text(
        "---\ntitle: Second capture\ntype: raw-source\nsource_query: second\n---\n\n"
        "Different source body.\n",
        encoding="utf-8",
    )

    with pytest.raises(CurationError, match="duplicate.*x-concept") as exc_info:
        plan_curation(raw, vault, synth)

    message = str(exc_info.value)
    assert "query.md" in message
    assert "second.md" in message


def test_normalize_markdown_joins_only_wrapped_prose():
    source = ("A wrapped paragraph\ncontinues with `inline code`.\n\n"
              "# Heading\n\n- list item\n  continuation\n\n"
              "> quoted\n> text\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n"
              "```python\nvalue = 1\nvalue += 1\n```\n\n"
              "A hard break  \ncontinues.")
    assert _normalize_markdown(source) == ("A wrapped paragraph continues with `inline code`.\n\n"
                                           "# Heading\n\n- list item\n  continuation\n\n"
                                           "> quoted\n> text\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n"
                                           "```python\nvalue = 1\nvalue += 1\n```\n\n"
                                           "A hard break  \ncontinues.")
