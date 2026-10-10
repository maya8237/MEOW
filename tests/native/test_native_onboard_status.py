import json
import subprocess

import pytest

from meow.project.onboarding import onboard_project
from tests.native.test_native_cli import run_native


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "meow.project.config.user_config_path", lambda: tmp_path / "none"
    )
    root = tmp_path / "r"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


def test_onboard_status_reports_not_onboarded(repo):
    code, out, _ = run_native("onboard-status", "--working-dir", str(repo))
    assert code == 0
    data = json.loads(out)
    assert set(data) == {"onboarded", "config_exists", "boundary_ok", "gaps"}
    assert data["onboarded"] is False
    assert data["gaps"]["jira"]["configured"] is False


def test_onboard_status_reports_onboarded(repo):
    onboard_project(repo)
    code, out, _ = run_native("onboard-status", "--working-dir", str(repo))
    data = json.loads(out)
    assert code == 0
    assert data["onboarded"] is True
    assert data["config_exists"] is True
    assert data["boundary_ok"] is True


def _committed_repo(root):
    for args in (
        ("config", "user.email", "t@example.com"),
        ("config", "user.name", "t"),
    ):
        subprocess.run(["git", *args], cwd=root, check=True)
    (root / "README.md").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)


def test_prepare_onboards_the_feature_worktree(repo):
    _committed_repo(repo)
    (repo / "pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "ruff"], cwd=repo, check=True)

    code, out, err = run_native("prepare", "--name", "feat", "--working-dir", str(repo))

    assert code == 0, err
    data = json.loads(out)
    active = repo / ".worktrees" / "feat"
    assert set(data["onboarding"]["files"]) == {
        ".gitignore",
        ".meow/config.toml",
        ".meow/config.local.toml",
    }
    assert (active / ".meow" / "config.toml").is_file()
    assert not (repo / ".meow" / "config.toml").exists()
    assert data["lint"][0]["command"] == "python -m ruff check"


def test_prepare_in_place_onboards_the_project(repo):
    _committed_repo(repo)

    code, out, err = run_native(
        "prepare", "--no-worktree", "--allow-dirty", "--working-dir", str(repo)
    )

    assert code == 0, err
    assert (repo / ".meow" / "config.toml").is_file()
    assert json.loads(out)["onboarding"]["files"]


def test_prepare_skips_an_onboarded_project(repo):
    _committed_repo(repo)
    onboard_project(repo)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "onboard"], cwd=repo, check=True)

    code, out, err = run_native("prepare", "--name", "feat", "--working-dir", str(repo))

    assert code == 0, err
    assert json.loads(out)["onboarding"] is None


def test_native_lint_reads_config_from_the_onboarded_worktree(repo):
    _committed_repo(repo)
    (repo / "pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "ruff"], cwd=repo, check=True)
    run_native("prepare", "--name", "feat", "--working-dir", str(repo))
    active = repo / ".worktrees" / "feat"

    code, out, _ = run_native(
        "verify", "--no-lint", "--working-dir", str(repo), "--active-dir", str(active)
    )

    assert code == 0
    assert json.loads(out)["lint"][0]["command"] == "python -m ruff check"
