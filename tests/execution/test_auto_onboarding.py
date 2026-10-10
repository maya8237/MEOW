"""`meow run` onboards a never-onboarded project as part of the run."""

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from meow.execution.orchestrator import Sprint
from meow.execution.run_state import RunStore
from meow.execution.sprint_runner import run_sprint
from meow.infrastructure.checks import config_fingerprint
from meow.project.onboarding import BOUNDARY, onboard_project

BASE_CONFIG = {
    "lint": [],
    "lint_timeout": 60,
    "max_rounds": 1,
    "models": {},
    "docs_dir": ".",
    "tester": {"tests": []},
    "worktree_setup": {"copy": [], "commands": []},
}


@pytest.fixture(autouse=True)
def _no_user_config(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "meow.project.config.user_config_path", lambda: tmp_path / "no-user-config"
    )


def _git_dir(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    return path


def _run(active: Path, *, record_root: Path | None = None):
    plan = active / "plan.md"
    plan.write_text("# Plan\n", encoding="utf-8")
    sprint = Sprint(record_root or active, dict(BASE_CONFIG), None, None, active, False)
    with (
        patch(
            "meow.execution.sprint_runner._prepare_sprint",
            return_value=(sprint, "feature", active),
        ),
        patch(
            "meow.execution.sprint_runner._run_review_rounds",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "meow.execution.sprint_runner.run_final_checks",
            new_callable=AsyncMock,
            return_value=[],
        ) as final_checks,
        patch("meow.execution.sprint_runner.configured_checks", return_value=[]),
        patch("meow.execution.sprint_runner.completion_ready", return_value=True),
        patch("meow.execution.sprint_runner.deliver_verified_run"),
    ):
        asyncio.run(
            run_sprint(
                active,
                None,
                "review the change",
                use_worktree=False,
                plan_file=plan,
                resume_at="review",
                record_root=record_root,
            )
        )
    return final_checks


def _phases(root: Path) -> list[str]:
    return [item["phase"] for item in RunStore(root).latest().transitions]


def test_first_run_onboards_active_dir(tmp_path):
    repo = _git_dir(tmp_path / "repo")
    (repo / "pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")

    _run(repo)

    assert (repo / ".meow" / "config.toml").is_file()
    assert BOUNDARY[0] in (repo / ".gitignore").read_text(encoding="utf-8")
    record = RunStore(repo).latest()
    assert set(record.results["onboarding"]["files"]) == {
        ".gitignore",
        ".meow/config.toml",
    }
    assert "onboarding" in _phases(repo)


def test_onboarded_config_reaches_final_checks(tmp_path):
    repo = _git_dir(tmp_path / "repo")
    (repo / "pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")

    final_checks = _run(repo)

    config = final_checks.await_args.args[1]
    assert config["lint"]
    assert RunStore(repo).latest().config_fingerprint == config_fingerprint(repo)


def test_already_onboarded_project_skips_phase(tmp_path):
    repo = _git_dir(tmp_path / "repo")
    onboard_project(repo)
    before = (repo / ".meow" / "config.toml").read_bytes()

    _run(repo)

    assert "onboarding" not in _phases(repo)
    assert "onboarding" not in RunStore(repo).latest().results
    assert (repo / ".meow" / "config.toml").read_bytes() == before


def test_worktree_with_onboarded_main_checkout_only_repairs_boundary(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".meow").mkdir()
    (repo / ".meow" / "config.toml").write_text("max_rounds = 1\n", encoding="utf-8")
    active = _git_dir(tmp_path / "worktree")

    final_checks = _run(active, record_root=repo)

    assert BOUNDARY[0] in (active / ".gitignore").read_text(encoding="utf-8")
    assert not (active / ".meow" / "config.toml").exists()
    assert final_checks.await_args.args[1]["max_rounds"] == 1
    assert RunStore(repo).latest().config_fingerprint == config_fingerprint(repo)


def test_onboarding_failure_does_not_fail_run(tmp_path):
    repo = _git_dir(tmp_path / "repo")

    with patch(
        "meow.execution.sprint_runner.onboard_project", side_effect=OSError("disk")
    ):
        _run(repo)

    record = RunStore(repo).latest()
    assert record.phase == "complete"
    assert "disk" in record.results["onboarding"]["error"]
