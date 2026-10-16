import asyncio
import json
import subprocess

import pytest

from meow.agents.base import ProjectContext
from meow.agents.reviewer import ReviewerAgent, _verdict_status


@pytest.fixture
def mixed_repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for name, content in {
        "backend/api.py": "def serve(): return 'ok'\n",
        "frontend/app.tsx": "export const App = () => <main>Hello</main>;\n",
        "scripts/run.sh": "echo ready\n",
        "skills/review/SKILL.md": "Review the project.\n",
        "tests/test_api.py": "assert True\n",
        "package.json": '{"private":true}\n',
    }.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "backend"], check=True)
    return tmp_path


def coverage(root, *, omit=()):
    paths = {
        "backend": "backend/api.py",
        "frontend": "frontend/app.tsx",
        "scripts": "scripts/run.sh",
        "skills": "skills/review/SKILL.md",
        "tests": "tests/test_api.py",
        ".": "package.json",
    }
    return "SUMMARY: reviewed\nSTATUS: PASS\n" + "\n".join(
        "COVERAGE: "
        + json.dumps({
            "area": area,
            "path": path,
            "line": 1,
            "excerpt": (root / path).read_text(encoding="utf-8").strip(),
        })
        for area, path in paths.items()
        if area not in omit
    )


def test_sdk_whole_project_omitted_frontend_forces_persisted_fail(
    mixed_repo, monkeypatch
):
    config = {"docs_dir": ".meow/plans", "models": {"reviewer": None}, "lint": []}
    agent = ReviewerAgent(ProjectContext(mixed_repo, config))
    review_file = mixed_repo / ".meow/plans/prompt.test.review.md"
    monkeypatch.setattr(
        "meow.agents.reviewer.new_review_filename", lambda _: review_file.name
    )

    async def write_report(*_args):  # ruff: ignore[unused-async] -- SDK boundary
        review_file.write_text(
            coverage(mixed_repo, omit=("frontend",)), encoding="utf-8"
        )

    monkeypatch.setattr(agent, "run_definition_query", write_report)
    status, report = asyncio.run(
        agent.review_prompt("Review the entire repository from zero")
    )
    assert status == "FAIL"
    assert "frontend" in report
    assert _verdict_status(review_file.read_text(encoding="utf-8")) == "FAIL"


def test_inventory_includes_mixed_areas_and_excludes_private_generated_and_ignored(
    mixed_repo,
):
    from meow.project.review_scope import resolve_scope

    for name in (
        ".gitignore",
        "ignored/deep.py",
        "node_modules/pkg/index.js",
        ".venv/site.py",
        "frontend/dist/bundle.js",
        ".meow/plans/report.md",
        "docs/output/report.md",
        "frontend/.env.local",
        "backend/key.pem",
        "__pycache__/cache.pyc",
    ):
        path = mixed_repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ignored/\n", encoding="utf-8")
    scope = resolve_scope(mixed_repo, "docs/output", "entire project", True)
    assert set(scope["areas"]) == {
        "backend",
        "frontend",
        "scripts",
        "skills",
        "tests",
        "docs",
        ".",
    }
    assert set(scope["files"]) == {
        "backend/api.py",
        "frontend/app.tsx",
        "scripts/run.sh",
        "skills/review/SKILL.md",
        "tests/test_api.py",
        "package.json",
        ".gitignore",
        "docs/output/report.md",
    }


@pytest.mark.parametrize(
    "basis,has_diff,explicit,expected",
    [
        ("", False, "auto", "project"),
        ("", True, "auto", "diff"),
        ("Review all code from scratch", True, "auto", "project"),
        ("Review this project from zero", True, "auto", "project"),
        ("review from 0", True, "auto", "project"),
        ("Check the entire repository", True, "auto", "project"),
        ("Check the login change", False, "auto", "diff"),
        ("Check the login change", True, "project", "project"),
        ("Review everything", False, "diff", "diff"),
    ],
)
def test_scope_resolution(mixed_repo, basis, has_diff, explicit, expected):  # ruff: ignore[too-many-arguments, too-many-positional-arguments] -- parameterized cases
    from meow.project.review_scope import resolve_scope

    assert (
        resolve_scope(mixed_repo, ".meow", basis, has_diff, explicit)["mode"]
        == expected
    )


def test_grounded_complete_coverage_passes_and_fabricated_excerpt_fails(mixed_repo):
    from meow.project.review_scope import coverage_errors, prepare_scope

    report = mixed_repo / ".meow/review.md"
    prepare_scope(report, mixed_repo, ".meow", "whole project", True)
    text = coverage(mixed_repo)
    assert coverage_errors(report, text) == []
    assert coverage_errors(
        report, text.replace("<main>Hello</main>", "<main>Fabricated</main>")
    )
    assert any(
        "frontend" in error
        for error in coverage_errors(report, coverage(mixed_repo, omit=("frontend",)))
    )


@pytest.mark.parametrize("change", ["modify", "add", "delete"])
def test_stale_inventory_cannot_pass(mixed_repo, change):
    from meow.project.review_scope import coverage_errors, prepare_scope

    report = mixed_repo / ".meow/review.md"
    prepare_scope(report, mixed_repo, ".meow", "whole project", True)
    text = coverage(mixed_repo)
    if change == "modify":
        (mixed_repo / "frontend/app.tsx").write_text("changed", encoding="utf-8")
    elif change == "add":
        (mixed_repo / "frontend/new.tsx").write_text("new", encoding="utf-8")
    else:
        (mixed_repo / "frontend/app.tsx").unlink()
    assert any("changed" in error.lower() for error in coverage_errors(report, text))


def test_binary_and_empty_only_directories_do_not_require_source_excerpts(mixed_repo):
    from meow.project.review_scope import resolve_scope

    (mixed_repo / "assets").mkdir()
    (mixed_repo / "assets/image.png").write_bytes(b"\x89PNG\0\xff")
    (mixed_repo / "empty").mkdir()
    (mixed_repo / "empty/.keep").touch()
    scope = resolve_scope(mixed_repo, ".meow", "whole project", True)
    assert "assets" not in scope["areas"]
    assert "empty" not in scope["areas"]


def test_project_scope_includes_shared_meow_definitions(mixed_repo):
    from meow.project.review_scope import resolve_scope

    for name in (
        ".meow/agents/reviewer.toml",
        ".meow/workflows/default.toml",
        ".meow/skills/check/SKILL.md",
        ".meow/runs/runtime.json",
    ):
        path = mixed_repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("shared definition")
    scope = resolve_scope(mixed_repo, ".meow/plans", "whole project", False)
    assert {".meow/agents", ".meow/workflows", ".meow/skills"} <= scope["areas"].keys()
    assert ".meow/runs/runtime.json" not in scope["files"]


def test_diff_snapshot_rejects_changed_source(mixed_repo):
    from meow.project.review_scope import coverage_errors, prepare_scope

    report = mixed_repo / ".meow/review.md"
    prepare_scope(report, mixed_repo, ".meow/plans", "login diff", True, "diff")
    (mixed_repo / "frontend/app.tsx").write_text("changed")
    assert coverage_errors(report, "SUMMARY: old\nSTATUS: PASS\n")


def test_root_report_directory_cannot_hide_project_source(mixed_repo):
    from meow.project.review_scope import resolve_scope

    scope = resolve_scope(mixed_repo, ".", "whole project", True)
    assert "backend/api.py" in scope["files"]
    assert "frontend/app.tsx" in scope["files"]
