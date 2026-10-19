"""One run entry point selects sprint or multi-sprint execution explicitly."""

import pytest

from meow.cli.cli import cli_main
from meow.cli.parser import _build_arg_parser
from meow.cli.validation import _validate_run_flags
from meow.execution.orchestration.model import OrchestratorTask
from meow.execution.orchestration.store import OrchestratorStore
from meow.execution.run_policy import uses_orchestration
from meow.execution.run_state import RunStore

EXIT_USAGE = 2


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["Build an API", "--name", "api"], False),
        (["--jira"], False),
        (["--jira", "P-1"], False),
        (["Build an API", "--orchestrate"], True),
        (["Build an API", "-o"], True),
        (["--jira", "P-1", "P-2"], True),
        (["--jira", "P-1", "--orchestrate"], True),
        (["--tasks", "tasks.toml"], True),
        (["--jql", "project = P"], True),
    ],
)
def test_run_selects_engine(argv, expected):
    parser = _build_arg_parser()
    args = parser.parse_args(["run", *argv])
    _validate_run_flags(parser, args)
    assert uses_orchestration(args) is expected


@pytest.mark.parametrize("command", ["orchestrate", "o"])
def test_removed_commands_are_rejected(command):
    with pytest.raises(SystemExit) as info:
        cli_main([command, "Build an API"])
    assert info.value.code == EXIT_USAGE


@pytest.mark.parametrize(
    "flags",
    [
        ["--name", "api"],
        ["--no-worktree"],
        ["--plan", "plan.md"],
        ["--resume-at", "review"],
        ["--workflow", "quick"],
        ["--input", "topic=API"],
        ["--manually-approve-plan"],
        ["--background"],
        ["--lint-fix"],
        ["--report-only"],
    ],
)
def test_orchestration_rejects_sprint_only_flags(flags):
    parser = _build_arg_parser()
    args = parser.parse_args(["run", "Build an API", "--orchestrate", *flags])
    with pytest.raises(SystemExit) as info:
        _validate_run_flags(parser, args)
    assert info.value.code == EXIT_USAGE


@pytest.mark.parametrize("flag", ["--max-parallel", "--dry-run", "--no-pr"])
def test_supervisor_options_do_not_silently_change_engine(flag):
    parser = _build_arg_parser()
    values = [flag, "2"] if flag == "--max-parallel" else [flag]
    args = parser.parse_args(["run", "Build an API", "--name", "api", *values])
    with pytest.raises(SystemExit) as info:
        _validate_run_flags(parser, args)
    assert info.value.code == EXIT_USAGE


@pytest.fixture
def orchestration(tmp_path):
    return OrchestratorStore(tmp_path).create(
        objective="Build the API and UI",
        source="objective",
        base_branch="main",
        mode="cli",
        max_parallel=2,
        options={"pr": False, "test": False},
    )


@pytest.mark.parametrize("command", ["status", "resume"])
def test_lifecycle_inspects_orchestration_by_id(
    tmp_path, orchestration, capsys, command
):
    with pytest.raises(SystemExit) as info:
        cli_main([command, orchestration.id, "-d", str(tmp_path)])
    assert info.value.code == 0
    assert orchestration.id in capsys.readouterr().out
    assert OrchestratorStore(tmp_path).load(orchestration.id).phase == "decomposing"


def test_cancel_routes_to_orchestration(tmp_path, orchestration):
    cli_main(["cancel", orchestration.id, "-d", str(tmp_path)])
    assert OrchestratorStore(tmp_path).load(orchestration.id).phase == "cancelled"


def test_resume_requires_continue_before_resetting_task(
    tmp_path, orchestration, capsys
):
    with pytest.raises(SystemExit) as info:
        cli_main(["resume", orchestration.id, "--task", "api", "-d", str(tmp_path)])
    assert info.value.code != 0
    assert "--continue" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["status", "resume"])
def test_latest_execution_selects_parent_instead_of_child(
    tmp_path, orchestration, capsys, command
):
    child = RunStore(tmp_path).create(
        source="orchestrator",
        request="Build API",
        repo=tmp_path,
        worktree=tmp_path,
        branch="task-api",
    )
    OrchestratorStore(tmp_path).update(
        orchestration.id,
        lambda record: record.tasks.append(
            OrchestratorTask("api", "Build API", run_ids=[child.id])
        ),
    )
    with pytest.raises(SystemExit) as info:
        cli_main([command, "-d", str(tmp_path)])
    assert info.value.code == 0
    assert f"Orchestrator {orchestration.id}" in capsys.readouterr().out


def test_removed_native_command_is_rejected():
    with pytest.raises(SystemExit) as info:
        cli_main(["native", "orchestrate", "status"])
    assert info.value.code == EXIT_USAGE
