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


@pytest.mark.parametrize("flag", ["--max-parallel", "--dry-run"])
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


@pytest.mark.parametrize(
    ("value", "key"),
    [
        ("proj-1", "PROJ-1"),
        ("https://acme.atlassian.net/browse/PROJ-12", "PROJ-12"),
        ("https://acme.atlassian.net/browse/PROJ-12/", "PROJ-12"),
        (
            "https://acme.atlassian.net/jira/software/projects/PROJ/boards/1"
            "?selectedIssue=PROJ-3",
            "PROJ-3",
        ),
    ],
)
def test_jira_accepts_keys_and_issue_urls(value, key):
    parser = _build_arg_parser()
    assert parser.parse_args(["run", "--jira", value]).jira == key
    assert parser.parse_args(["review", "--jira", value]).jira == key


@pytest.mark.parametrize(
    "argv",
    [
        ["--jira", "P-1", "Keep the API stable"],
        ["Keep the API stable", "--jira", "P-1"],
        ["--jira", "P-1", "--", "Keep the API stable"],
    ],
)
def test_single_jira_issue_takes_extra_instructions(argv):
    parser = _build_arg_parser()
    args = parser.parse_args(["run", *argv])
    _validate_run_flags(parser, args)
    assert (args.jira, args.request) == ("P-1", "Keep the API stable")
    assert not uses_orchestration(args)


@pytest.mark.parametrize(
    "argv",
    [
        ["--jira", "not a key", "also text"],
        ["first text", "--jira", "P-1", "second text"],
        ["--jira", "https://acme.atlassian.net/browse/"],
        ["--jira", "P1"],
    ],
)
def test_jira_rejects_unclear_sources(argv):
    with pytest.raises(SystemExit) as info:
        _build_arg_parser().parse_args(["run", *argv])
    assert info.value.code == EXIT_USAGE


def test_orchestrated_sources_take_extra_instructions():
    parser = _build_arg_parser()
    for argv in (
        ["--jira", "P-1", "P-2", "Extra text"],
        ["--jql", "project = P", "Extra text"],
        ["--tasks", "tasks.toml", "Extra text"],
        ["-o", "--gitlab", "https://gitlab.com/g/p/-/issues/3", "Extra text"],
        ["-o", "--jira", "P-1", "Extra text"],
    ):
        args = parser.parse_args(["run", *argv])
        _validate_run_flags(parser, args)
        assert uses_orchestration(args)
        assert args.request == "Extra text"


@pytest.mark.parametrize("provider", ["gitlab", "github"])
def test_link_sources_build_one_item_with_instructions(provider):
    from meow.execution.run_policy import fetched_ref, fetched_source

    link = f"https://{provider}.com/o/r/issues/5"
    parser = _build_arg_parser()
    args = parser.parse_args(["run", f"--{provider}", link, "Keep it small"])
    _validate_run_flags(parser, args)
    assert (fetched_source(args), fetched_ref(args)) == (provider, link)
    assert args.request == "Keep it small"
    assert not uses_orchestration(args)


@pytest.mark.parametrize(
    "argv",
    [
        ["--gitlab", "not-a-url"],
        ["--gitlab", "https://g/x", "--github", "https://h/y"],
        ["--gitlab", "https://g/x", "--name", "n"],
        ["--lint-fix", "--github", "https://h/y"],
    ],
)
def test_link_sources_reject_bad_combinations(argv):
    parser = _build_arg_parser()
    with pytest.raises(SystemExit):
        _validate_run_flags(parser, parser.parse_args(["run", *argv]))
