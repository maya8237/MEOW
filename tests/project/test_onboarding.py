"""Deterministic first-run onboarding: ignore boundary, config, lint detection."""

import json
import subprocess
import tomllib
from pathlib import Path

import pytest

from meow.project.config import config_root, load_config
from meow.project.onboarding import (
    BOUNDARY,
    boundary_ok,
    detect_lint,
    feature_gaps,
    is_onboarded,
    onboard_project,
    repair_boundary,
)

FALLBACK_ROUNDS = 3


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
    result = subprocess.run(["git", "check-ignore", "-q", rel], cwd=root, check=False)
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
    assert set(report.files) == {
        ".gitignore",
        ".meow/config.toml",
        ".meow/config.local.toml",
    }
    text = (repo / ".meow" / "config.toml").read_text(encoding="utf-8")
    assert 'command = "python -m ruff check"' in text
    assert 'fix_flag = "--fix"' in text
    assert load_config(repo)["lint"]
    assert not _ignored(repo, ".meow/config.toml")
    assert _ignored(repo, ".meow/config.local.toml")
    assert _ignored(repo, ".meow/runs/x.json")
    assert is_onboarded(repo)


def test_onboard_project_writes_documented_shared_and_local_configs(repo):
    onboard_project(repo)

    local_text = (repo / ".meow" / "config.local.toml").read_text(encoding="utf-8")
    shared_text = (repo / ".meow" / "config.toml").read_text(encoding="utf-8")
    assert tomllib.loads(local_text) == {}
    assert "local" in local_text.lower()
    assert "max_rounds" in shared_text
    assert "automatically" in shared_text.lower()


def test_onboard_project_never_rewrites_existing_local_config(repo):
    local = repo / ".meow" / "config.local.toml"
    local.parent.mkdir()
    local.write_text('docs_dir = "private-plans"\n', encoding="utf-8")

    onboard_project(repo)

    assert local.read_text(encoding="utf-8") == 'docs_dir = "private-plans"\n'


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
    assert set(report.files) == {".gitignore", ".meow/config.local.toml"}
    assert not (repo / ".meow" / "config.toml").exists()


def test_onboard_project_outside_git_repo_writes_no_config(tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    report = onboard_project(root)
    assert report.error and "git" in report.error
    assert not (root / ".meow" / "config.toml").exists()
    assert not (root / ".gitignore").exists()
    assert not (root / ".gitignore").exists()


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


def test_feature_gaps_reports_configured_and_missing(repo):
    _write_config(
        repo,
        '[jira]\nproject_key = "PROJ"\n\n[[tester.tests]]\ncommand = "pytest"\n',
    )
    gaps = feature_gaps(repo)
    assert gaps["jira"]["configured"] is True
    assert gaps["tester_tests"]["configured"] is True
    assert gaps["github"]["configured"] is False
    assert all(item["enable"] for item in gaps.values())
    assert set(gaps) == {
        "jira",
        "gitlab",
        "github",
        "tester_tests",
        "tester_mcp",
        "tester_browser",
        "build",
        "worktree_setup",
        "permissions",
        "agent_skills",
        "custom",
        "hooks",
        "architecture_doc",
    }


def test_feature_gaps_never_includes_secret_values(repo):
    _write_config(
        repo,
        '[github.mcp]\ncommand = "npx"\n[github.mcp.env]\n'
        'GITHUB_PERSONAL_ACCESS_TOKEN = "sekret"\n',
    )
    gaps = feature_gaps(repo)
    assert gaps["github"]["configured"] is True
    assert "sekret" not in json.dumps(gaps)


def test_feature_gaps_counts_local_config(repo):
    (repo / ".meow").mkdir()
    (repo / ".meow" / "config.local.toml").write_text(
        '[agent_skills]\ndefault = ["x"]\n', encoding="utf-8"
    )
    assert feature_gaps(repo)["agent_skills"]["configured"] is True


def test_feature_gaps_architecture_doc(repo):
    assert feature_gaps(repo)["architecture_doc"]["configured"] is False
    (repo / "docs").mkdir()
    (repo / "docs" / "ARCHITECTURE.md").write_text("# A\n", encoding="utf-8")
    assert feature_gaps(repo)["architecture_doc"]["configured"] is True


def test_feature_gaps_tolerates_malformed_config(repo):
    _write_config(repo, "this is not = = toml")
    assert feature_gaps(repo)["jira"]["configured"] is False


def test_config_root_prefers_project_then_onboarded_active(tmp_path):
    project, active = tmp_path / "p", tmp_path / "a"
    for path in (project, active):
        path.mkdir()
    assert config_root(project, active) == project
    _write_config(active)
    assert config_root(project, active) == active
    _write_config(project)
    assert config_root(project, active) == project
    assert config_root(project) == project


def test_config_does_not_override_user_fallback_settings(repo, tmp_path, monkeypatch):
    fallback = tmp_path / "fallback.toml"
    fallback.write_text(
        'max_rounds = 3\n[[lint]]\ncommand = "mylint"\n', encoding="utf-8"
    )
    monkeypatch.setattr("meow.project.config.user_config_path", lambda: fallback)
    (repo / "pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")

    onboard_project(repo)

    text = (repo / ".meow" / "config.toml").read_text(encoding="utf-8")
    assert "max_rounds" not in text
    assert "[[lint]]" not in text
    config = load_config(repo)
    assert config["max_rounds"] == FALLBACK_ROUNDS
    assert [c.command for c in config["lint"]] == ["mylint"]


def test_config_does_not_override_local_settings(repo):
    (repo / ".meow").mkdir()
    (repo / ".meow" / "config.local.toml").write_text(
        'docs_dir = "plans"\n', encoding="utf-8"
    )

    onboard_project(repo)

    text = (repo / ".meow" / "config.toml").read_text(encoding="utf-8")
    assert "docs_dir" not in text
    assert "max_rounds = 8" in text
