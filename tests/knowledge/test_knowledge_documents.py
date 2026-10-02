from meow.knowledge import AuditFinding, KnowledgeAudit, select_findings
from meow.knowledge_documents import EvidenceDocumentWriter, create_selected_documents


def finding(path="AGENTS.md"):
    return AuditFinding(
        "missing", path, ("evidence.md:1: observed",), "warning", False, "create", path
    )


def test_creation_requires_selection_and_preserves_existing(tmp_path):
    audit = KnowledgeAudit(tmp_path, (finding(),), ())
    assert (
        create_selected_documents(tmp_path, (), writer=EvidenceDocumentWriter()).created
        == ()
    )
    (tmp_path / "AGENTS.md").write_text("keep", encoding="utf-8")
    result = create_selected_documents(
        tmp_path,
        select_findings(audit, [audit.findings[0].id]),
        writer=EvidenceDocumentWriter(),
    )
    assert (
        result.skipped == ("AGENTS.md",)
        and (tmp_path / "AGENTS.md").read_text() == "keep"
    )


def test_unknown_or_duplicate_selection_fails_before_writes(tmp_path):
    audit = KnowledgeAudit(tmp_path, (finding(),), ())
    for ids in (("nope",), (audit.findings[0].id, audit.findings[0].id)):
        try:
            select_findings(audit, ids)
        except ValueError:
            pass
        else:
            raise AssertionError("expected missing selection to be rejected")
