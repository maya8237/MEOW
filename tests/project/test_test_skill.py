from pathlib import Path

from meow.project.custom.frontmatter import parse

SKILL = Path(__file__).resolve().parents[2] / "skills" / "test" / "SKILL.md"
MAX_LINES = 200


def test_test_skill_documents_workflow_and_worktree_rule():
    metadata, body = parse(SKILL)
    assert metadata["name"] == "test"
    assert metadata["description"].startswith("Use when")
    for phrase in (
        "--workflow test-fix",
        "--no-worktree",
        "uncommitted",
        "--preflight",
        "## Tester setup",
        "meow status",
        "meow resume",
    ):
        assert phrase in body, phrase
    assert len(SKILL.read_text(encoding="utf-8").splitlines()) < MAX_LINES


def test_docs_and_readme_list_the_test_skill():
    root = SKILL.parents[2]
    assert "/meow:test" in (root / "docs" / "SKILLS.md").read_text(encoding="utf-8")
    assert "/meow:test" in (root / "README.md").read_text(encoding="utf-8")
