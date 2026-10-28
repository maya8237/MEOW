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


@pytest.mark.parametrize("dev_ref", [None, "refs/heads/dev", "refs/remotes/origin/dev"])
def test_auto_onboarding_saves_detected_merge_target(repo, dev_ref):
    if dev_ref:
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@e.c",
                "commit",
                "--allow-empty",
                "-qm",
                "initial",
            ],
            cwd=repo,
            check=True,
        )
        subprocess.run(["git", "update-ref", dev_ref, "HEAD"], cwd=repo, check=True)
    report = onboard_project(repo)
    assert report.error is None
    raw = tomllib.loads((repo / ".meow/config.toml").read_text(encoding="utf-8"))
    assert raw["delivery"]["target_branch"] == ("dev" if dev_ref else "master")


def test_auto_onboarding_keeps_configured_target(repo):
    _write_config(repo, '[delivery]\ntarget_branch = "release"\n')
    onboard_project(repo)
    assert load_config(repo)["delivery"]["target_branch"] == "release"


@pytest.fixture(autouse=True)
def _no_user_config(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "meow.project.config.user_config_path", lambda: tmp_path / "no-user-config"
    )
    monkeypatch.setattr(
        "meow.project.repos.registry_path", lambda: tmp_path / "repos.toml"
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
    assert tuple(lines[-len(BOUNDARY) :]) == BOUNDARY
    assert boundary_ok(repo)


def test_repair_boundary_removes_obsolete_broad_rules(repo):
    (repo / ".gitignore").write_text(
        ".meow/\n/.meow\nnode_modules/\n.meow\n", encoding="utf-8"
    )
    repair_boundary(repo)
    lines = (repo / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert lines.count(".meow/") == 0
    assert "node_modules/" in lines
    assert tuple(lines[-len(BOUNDARY) :]) == BOUNDARY


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
    assert "[[lint]]" not in text  # Inference refreshes at load time, not in TOML.
    assert report.to_dict()["detected"]["lint"] == "pyproject.toml:tool.ruff"
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
        "tester_write_tests",
        "tester_mcp",
        "tester_browser",
        "tester_dev_server",
        "build",
        "lint",
        "models",
        "workflow",
        "orchestrator",
        "delivery",
        "worktree_setup",
        "permissions",
        "agent_skills",
        "custom",
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


def test_feature_gaps_discovers_additions_without_changing_existing_setup(repo):
    onboard_project(repo)
    shared = repo / ".meow" / "config.toml"
    original = shared.read_bytes()
    gaps = feature_gaps(repo)
    assert gaps["tester_dev_server"]["configured"] is False
    assert gaps["workflow"]["configured"] is False
    assert gaps["orchestrator"]["configured"] is False
    assert shared.read_bytes() == original
    local = repo / ".meow" / "config.local.toml"
    local.write_text(
        '[[tester.dev_server]]\ncommand = "npm run dev"\n'
        '[workflow]\ndefault = "my-workflow"\n'
        "[orchestrator]\nmax_parallel = 2\n",
        encoding="utf-8",
    )
    updated = feature_gaps(repo)
    assert updated["tester_dev_server"]["configured"] is True
    assert updated["workflow"]["configured"] is True
    assert updated["orchestrator"]["configured"] is True
    assert shared.read_bytes() == original


def test_feature_gaps_respects_local_empty_override_and_appended_skills(
    repo, tmp_path, monkeypatch
):
    fallback = tmp_path / "user.toml"
    fallback.write_text(
        '[[build]]\ncommand = "user-build"\n[agent_skills]\ndefault = ["user-skill"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("meow.project.config.user_config_path", lambda: fallback)
    _write_config(repo, '[[build]]\ncommand = "project-build"\n')
    local = repo / ".meow" / "config.local.toml"
    local.write_text("build = []\n[agent_skills]\ndefault = []\n", encoding="utf-8")

    gaps = feature_gaps(repo)

    assert gaps["build"]["configured"] is False
    assert gaps["agent_skills"]["configured"] is True


def test_feature_gaps_keeps_custom_sources_from_lower_layers(repo):
    _write_config(repo, '[custom]\nskills = ["team-skills"]\n')
    (repo / ".meow" / "config.local.toml").write_text(
        "[custom]\nskills = []\n", encoding="utf-8"
    )

    assert feature_gaps(repo)["custom"]["configured"] is True


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


def test_onboard_if_needed_registers_new_and_already_onboarded_repos(
    repo, tmp_path, monkeypatch
):
    from meow.project import repos
    from meow.project.onboarding import onboard_if_needed

    registry = tmp_path / "home" / "repos.toml"
    monkeypatch.setattr(repos, "registry_path", lambda: registry)

    assert onboard_if_needed(repo, repo) is not None
    assert str(repo.resolve()) in repos._saved()

    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=other, check=True)
    assert onboard_if_needed(other, other) is not None
    registry.unlink()
    assert onboard_if_needed(other, other) is None
    assert str(other.resolve()) in repos._saved()


def test_python_build_metadata_is_ignored_locally(repo):
    (repo / "pyproject.toml").write_text('[project]\nname = "demo"\n')
    metadata = repo / "demo.egg-info"
    metadata.mkdir()
    (metadata / "PKG-INFO").write_text("generated")

    onboard_project(repo)

    assert "demo.egg-info" not in (repo / ".gitignore").read_text()
    assert (
        subprocess.run(
            ["git", "check-ignore", "-q", "demo.egg-info/PKG-INFO"],
            cwd=repo,
            check=False,
        ).returncode
        == 0
    )
