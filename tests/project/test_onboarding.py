"""Deterministic first-run onboarding: ignore boundary, config, lint detection."""

import json
import subprocess
from pathlib import Path

import pytest

from meow.project.config import load_config
from meow.project.onboarding import (
    BOUNDARY,
    boundary_ok,
    detect_lint,
    is_onboarded,
    onboard_project,
    repair_boundary,
)


@pytest.fixture(autouse=True)
def _no_user_config(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "meow.project.config.user_config_path", lambda: tmp_path / "no-user-config"
    )


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


def _ignored(root: Path, rel: str) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "-q", rel], cwd=root, check=False
    )
    return result.returncode == 0


def _write_boundary(root: Path) -> None:
    (root / ".gitignore").write_text("\n".join(BOUNDARY) + "\n", encoding="utf-8")


def _write_config(root: Path, text: str = "max_rounds = 8\n") -> None:
    (root / ".meow").mkdir(exist_ok=True)
    (root / ".meow" / "config.toml").write_text(text, encoding="utf-8")


def test_is_onboarded_requires_config_and_boundary(repo):
    assert not is_onboarded(repo)
    _write_config(repo)
    assert not is_onboarded(repo)
    _write_boundary(repo)
    assert is_onboarded(repo)


def test_local_config_alone_is_not_onboarded(repo):
    _write_boundary(repo)
    (repo / ".meow").mkdir()
    (repo / ".meow" / "config.local.toml").write_text("", encoding="utf-8")
    assert not is_onboarded(repo)


def test_repair_boundary_appends_block_and_keeps_unrelated_rules(repo):
    (repo / ".gitignore").write_text("node_modules/\n*.log", encoding="utf-8")
    assert repair_boundary(repo) is True
    lines = (repo / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert lines[:2] == ["node_modules/", "*.log"]
    assert tuple(lines[-3:]) == BOUNDARY
    assert boundary_ok(repo)


def test_repair_boundary_removes_obsolete_broad_rules(repo):
    (repo / ".gitignore").write_text(
        ".meow/\n/.meow\nnode_modules/\n.meow\n", encoding="utf-8"
    )
    repair_boundary(repo)
    lines = (repo / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert lines.count(".meow/") == 0
    assert "node_modules/" in lines
    assert tuple(lines[-3:]) == BOUNDARY


def test_repair_boundary_preserves_crlf(repo):
    (repo / ".gitignore").write_bytes(b"node_modules/\r\n")
    repair_boundary(repo)
    data = (repo / ".gitignore").read_bytes()
    assert data.count(b"\n") == data.count(b"\r\n")


def test_repair_boundary_is_noop_when_correct(repo):
    _write_boundary(repo)
    before = (repo / ".gitignore").read_bytes()
    assert repair_boundary(repo) is False
    assert (repo / ".gitignore").read_bytes() == before


def test_onboard_project_writes_boundary_then_config(repo):
    (repo / "pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")
    report = onboard_project(repo)
    assert report.error is None
    assert set(report.files) == {".gitignore", ".meow/config.toml"}
    text = (repo / ".meow" / "config.toml").read_text(encoding="utf-8")
    assert 'command = "python -m ruff check"' in text
    assert 'fix_flag = "--fix"' in text
    assert load_config(repo)["lint"]
    assert not _ignored(repo, ".meow/config.toml")
    assert _ignored(repo, ".meow/config.local.toml")
    assert _ignored(repo, ".meow/runs/x.json")
    assert is_onboarded(repo)


def test_onboard_project_detects_eslint(repo):
    (repo / "package.json").write_text(
        json.dumps({"devDependencies": {"eslint": "^9"}}), encoding="utf-8"
    )
    assert detect_lint(repo) == ("npx eslint", "--fix")


def test_ruff_wins_over_eslint(repo):
    (repo / "pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")
    (repo / "package.json").write_text(
        json.dumps({"devDependencies": {"eslint": "^9"}}), encoding="utf-8"
    )
    assert detect_lint(repo)[0] == "python -m ruff check"


def test_onboard_project_without_linter_writes_no_lint_table(repo):
    report = onboard_project(repo)
    text = (repo / ".meow" / "config.toml").read_text(encoding="utf-8")
    assert "[[lint]]" not in text
    assert report.lint is None
    assert load_config(repo)["lint"] == []


def test_onboard_project_never_overwrites_config(repo):
    _write_config(repo, "max_rounds = 3\n")
    onboard_project(repo)
    text = (repo / ".meow" / "config.toml").read_text(encoding="utf-8")
    assert text == "max_rounds = 3\n"


def test_onboard_project_write_config_false_only_repairs_boundary(repo):
    report = onboard_project(repo, write_config=False)
    assert report.files == (".gitignore",)
    assert not (repo / ".meow" / "config.toml").exists()


def test_onboard_project_outside_git_repo_writes_no_config(tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    report = onboard_project(root)
    assert report.error and "git" in report.error
    assert not (root / ".meow" / "config.toml").exists()


def test_onboard_project_is_idempotent(repo):
    onboard_project(repo)
    second = onboard_project(repo)
    assert second.files == ()
    assert second.error is None


def test_report_lists_skipped_optional_features(repo):
    skipped = set(onboard_project(repo).skipped)
    assert {
        "Jira",
        "GitLab",
        "GitHub",
        "Tester mode",
        "Claude hooks",
        "Worktree setup",
    } <= skipped
