from pathlib import Path

from meow.knowledge import KnowledgeConfig, audit_project, structural_check


def test_audit_reports_missing_and_broken_evidence_without_writing(tmp_path: Path):
    docs = tmp_path / "docs"; docs.mkdir()
    source = docs / "guide.md"; source.write_text("See [gone](missing.md) and [outside](../other.md).\n", encoding="utf-8")
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    audit = audit_project(tmp_path, config=KnowledgeConfig(required_paths=("AGENTS.md",), docs_roots=("docs",)))
    after = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    assert before == after
    assert any(f.kind == "missing" for f in audit.findings)
    broken = [f for f in audit.findings if f.kind == "broken_link"]
    assert len(broken) == 2 and all(not f.advisory and "guide.md:1" in f.evidence[0] for f in broken)


def test_structural_check_ignores_advisory_drift(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("# Rules\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/a.md").write_text("The module must always match the API.\n", encoding="utf-8")
    result = structural_check(tmp_path, audit_project(tmp_path))
    assert result["passed"] is False
    assert result["advisory_finding_ids"]
