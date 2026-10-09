"""Repository configuration remains authoritative for linked worktrees."""

import asyncio
from unittest.mock import AsyncMock, patch

from meow.execution.orchestrator import _prepare_sprint
from meow.execution.run_state import RunStore
from meow.execution.sprint import Sprint
from meow.execution.sprint_runner import run_sprint
from meow.infrastructure.checks import config_fingerprint


def test_prepare_sprint_loads_config_from_repository_root(tmp_path):
    repo = tmp_path / "repo"
    active = tmp_path / "worktree"
    repo.mkdir()
    active.mkdir()
    config = {"lint": []}

    with (
        patch("meow.execution.orchestrator.load_config", return_value=config) as load,
        patch(
            "meow.execution.orchestrator._resolve_working_dir",
            return_value=(active, "feature", True),
        ),
        patch("meow.execution.orchestrator.build_sprint", return_value=object()),
    ):
        result = _prepare_sprint(
            repo,
            "feature",
            use_worktree=True,
            config_dir=repo,
        )

    load.assert_called_once_with(repo)
    assert result[1:] == ("feature", active)


def test_run_sprint_checks_active_worktree_with_repository_config(tmp_path):
    repo = tmp_path / "repo"
    active = tmp_path / "worktree"
    repo.mkdir()
    active.mkdir()
    (repo / ".meow").mkdir()
    (repo / ".meow" / "config.toml").write_text("max_rounds = 1\n", encoding="utf-8")
    plan_file = active / "plan.md"
    plan_file.write_text("# Plan\n", encoding="utf-8")
    config = {
        "lint": [],
        "max_rounds": 1,
        "models": {},
        "docs_dir": ".",
        "tester": {"tests": []},
        "worktree_setup": {"copy": [], "commands": []},
    }
    sprint = Sprint(repo, config, None, None, active, True)

    with (
        patch("meow.project.config.user_config_path", return_value=tmp_path / "none"),
        patch(
            "meow.execution.sprint_runner._prepare_sprint",
            return_value=(sprint, "feature", active),
        ) as prepare,
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
                plan_file=plan_file,
                resume_at="review",
                record_root=repo,
            )
        )

    prepare.assert_called_once_with(
        active,
        None,
        use_worktree=False,
        source_branch=None,
        config_dir=repo,
    )
    final_checks.assert_awaited_once_with(active, config)
    assert RunStore(repo).latest().config_fingerprint == config_fingerprint(repo)
