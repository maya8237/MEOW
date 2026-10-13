"""`meow orchestrate`: argument rules, dispatch, and the printed results."""

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from meow.cli import orchestrate_cli
from meow.cli.cli import cli_main
from meow.cli.orchestrate_cli import EXIT_CANCELLED, orchestrate_command
from meow.cli.parser import _build_arg_parser
from meow.execution.orchestration.model import OrchestratorTask
from meow.execution.orchestration.report import render_report, render_status
from meow.execution.orchestration.store import OrchestratorStore

EXIT_BLOCKED = 2
EXIT_NO_PR = 3
PARALLEL = 2
SENTINEL_EXIT = 7


def git(directory: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=directory, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def parse(*argv: str):
    return _build_arg_parser().parse_args(["orchestrate", *argv])


@pytest.fixture
def repo(tmp_path):
    """A clean one-commit git repository."""
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q")
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.com")
    (path / ".gitignore").write_text(
        ".meow/*\n!.meow/\n!.meow/config.toml\n", encoding="utf-8"
    )
    git(path, "add", ".gitignore")
    git(path, "commit", "-qm", "initial")
    return path


def make_orchestrator(repo: Path, *, phase: str = "running", tasks=(), **fields):
    store = OrchestratorStore(repo)
    created = store.create(
        objective="Add login",
        source="objective",
        base_branch="main",
        mode="cli",
        max_parallel=3,
        options={"test": False, "pr": True},
    )

    def mutate(record):
        record.phase = phase
        record.tasks = list(tasks)
        record.integration["branch"] = f"orchestrator/{record.id}"
        for key, value in fields.items():
            setattr(record, key, value)

    return store.update(created.id, mutate)


def two_tasks():
    return [
        OrchestratorTask("api", "Build the API", status="merged"),
        OrchestratorTask("ui", "Build the UI", status="merged"),
    ]


def fake_start(orch_id: str, code: int, calls: list):
    async def start(repo, intake, **kwargs):
        await asyncio.sleep(0)
        calls.append({"intake": intake, **kwargs})
        return orch_id, code

    return start


def never_called(*args, **kwargs):
    raise AssertionError("the orchestrator must not start")


def delivered(repo: Path, **fields):
    return make_orchestrator(
        repo,
        phase="delivered",
        tasks=two_tasks(),
        delivery={
            "pushed_commit": "abc",
            "pr": {"url": "https://x/1", "provider": "github"},
            "pr_error": None,
        },
        **fields,
    )


@pytest.mark.parametrize(
    "argv",
    [
        (),
        ("Add login", "--jira", "PROJ-1"),
        ("--jira", "PROJ-1", "--jql", "project = PROJ"),
        ("--tasks", "tasks.toml", "Add login"),
    ],
)
def test_requires_exactly_one_source(repo, capsys, monkeypatch, argv):
    monkeypatch.setattr(orchestrate_cli, "start_orchestrator", never_called)

    assert orchestrate_command(parse(*argv), repo) == orchestrate_cli.EXIT_USAGE

    assert "exactly one of" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (("Add login", "--task", "api"), "only apply to `orchestrate resume`"),
        (("status", "--request", "x"), "only apply to `orchestrate resume`"),
        (("cancel", "abc", "--dry-run"), "cannot be used with `orchestrate cancel`"),
        (
            ("resume", "abc", "--jira", "P-1"),
            "cannot be used with `orchestrate resume`",
        ),
        (("resume",), "needs exactly one orchestrator ID"),
        (("status", "a", "b"), "at most one orchestrator ID"),
    ],
)
def test_invalid_combinations_are_usage_errors(repo, capsys, argv, message):
    assert orchestrate_command(parse(*argv), repo) == orchestrate_cli.EXIT_USAGE

    assert message in capsys.readouterr().err


def test_missing_tasks_file_is_a_usage_error(repo, capsys):
    assert (
        orchestrate_command(parse("--tasks", "nope.toml"), repo)
        == orchestrate_cli.EXIT_USAGE
    )

    assert "existing file" in capsys.readouterr().err


@pytest.mark.parametrize("flags", [("--jira", "PROJ-1"), ("--jql", "project = PROJ")])
def test_jira_sources_need_jira_config(repo, capsys, monkeypatch, flags):
    monkeypatch.setattr(orchestrate_cli, "start_orchestrator", never_called)

    assert orchestrate_command(parse(*flags), repo) == orchestrate_cli.EXIT_FAILED

    assert "[jira.mcp]" in capsys.readouterr().err


def test_dirty_tree_is_refused(repo, capsys, monkeypatch):
    monkeypatch.setattr(orchestrate_cli, "start_orchestrator", never_called)
    (repo / "dirty.txt").write_text("x", encoding="utf-8")

    assert orchestrate_command(parse("Add login"), repo) == orchestrate_cli.EXIT_FAILED

    assert "uncommitted" in capsys.readouterr().err


def test_objective_words_are_joined(repo, monkeypatch):
    record = delivered(repo)
    calls = []
    monkeypatch.setattr(
        orchestrate_cli, "start_orchestrator", fake_start(record.id, 0, calls)
    )

    orchestrate_command(parse("Add", "login", "page"), repo)

    assert calls[0]["intake"].source == "objective"
    assert calls[0]["intake"].objective == "Add login page"


def test_action_word_inside_a_quoted_objective_stays_an_objective(repo, monkeypatch):
    record = delivered(repo)
    calls = []
    monkeypatch.setattr(
        orchestrate_cli, "start_orchestrator", fake_start(record.id, 0, calls)
    )

    orchestrate_command(parse("status page rework"), repo)

    assert calls[0]["intake"].objective == "status page rework"


def test_start_passes_the_options_through(repo, monkeypatch):
    record = delivered(repo)
    calls = []
    monkeypatch.setattr(
        orchestrate_cli, "start_orchestrator", fake_start(record.id, 0, calls)
    )

    orchestrate_command(
        parse(
            "Add login",
            "--from",
            "release",
            "--max-parallel",
            "2",
            "--test",
            "--no-pr",
            "--dry-run",
        ),
        repo,
    )
    orchestrate_command(parse("Add login"), repo)

    first, second = calls
    assert first["base_branch"] == "release"
    assert first["max_parallel"] == PARALLEL
    assert first["test"] is True
    assert first["open_pr"] is False
    assert first["dry_run"] is True
    assert second["base_branch"] is None
    assert second["max_parallel"] is None
    assert second["test"] is False
    assert second["open_pr"] is None
    assert second["dry_run"] is False


def test_jira_keys_and_tasks_file_reach_the_intake(repo, monkeypatch):
    record = delivered(repo)
    calls = []
    monkeypatch.setattr(
        orchestrate_cli, "start_orchestrator", fake_start(record.id, 0, calls)
    )
    monkeypatch.setattr(orchestrate_cli, "_jira_problem", lambda args, wd: None)
    tasks_file = repo.parent / "tasks.toml"
    tasks_file.write_text("[[tasks]]\n", encoding="utf-8")

    orchestrate_command(parse("--jira", "PROJ-1", "PROJ-2"), repo)
    orchestrate_command(parse("--tasks", str(tasks_file)), repo)

    assert calls[0]["intake"].jira_keys == ("PROJ-1", "PROJ-2")
    assert calls[1]["intake"].tasks_file == tasks_file.resolve()


def test_max_parallel_must_be_positive(capsys):
    with pytest.raises(SystemExit):
        parse("Add login", "--max-parallel", "0")

    assert "must be an integer >= 1" in capsys.readouterr().err


def test_delivered_prints_one_json_line(repo, capsys, monkeypatch):
    record = delivered(repo)
    monkeypatch.setattr(
        orchestrate_cli, "start_orchestrator", fake_start(record.id, 0, [])
    )

    assert orchestrate_command(parse("Add login"), repo) == 0

    output = capsys.readouterr()
    assert json.loads(output.out.splitlines()[-1]) == {
        "orchestrator": record.id,
        "branch": f"orchestrator/{record.id}",
        "pr": "https://x/1",
        "tasks": 2,
    }
    assert "PR not opened" not in output.err


def test_delivered_without_pr_reports_reason_and_link(repo, capsys, monkeypatch):
    git(repo, "remote", "add", "origin", "https://github.com/o/r.git")
    record = make_orchestrator(
        repo,
        phase="delivered",
        tasks=two_tasks(),
        delivery={
            "pushed_commit": "abc",
            "pr": None,
            "pr_error": "provider not configured",
        },
    )
    monkeypatch.setattr(
        orchestrate_cli, "start_orchestrator", fake_start(record.id, EXIT_NO_PR, [])
    )

    assert orchestrate_command(parse("Add login"), repo) == EXIT_NO_PR

    output = capsys.readouterr()
    assert "PR not opened: provider not configured" in output.err
    assert (
        f"https://github.com/o/r/compare/main...orchestrator/{record.id}" in output.err
    )
    assert json.loads(output.out.splitlines()[-1])["pr"] is None


def test_no_pr_run_prints_no_pr_warning(repo, capsys, monkeypatch):
    record = make_orchestrator(
        repo,
        phase="delivered",
        tasks=two_tasks(),
        options={"test": False, "pr": False},
        delivery={"pushed_commit": "abc", "pr": None, "pr_error": None},
    )
    monkeypatch.setattr(
        orchestrate_cli, "start_orchestrator", fake_start(record.id, 0, [])
    )

    assert orchestrate_command(parse("Add login", "--no-pr"), repo) == 0

    assert "PR not opened" not in capsys.readouterr().err


def test_blocked_prints_the_report(repo, capsys, monkeypatch):
    record = make_orchestrator(
        repo,
        phase="blocked",
        tasks=two_tasks(),
        blocker={
            "task_id": "api",
            "cause": "needs-human",
            "evidence": [],
            "needed": "credentials",
        },
    )
    monkeypatch.setattr(
        orchestrate_cli, "start_orchestrator", fake_start(record.id, EXIT_BLOCKED, [])
    )

    assert orchestrate_command(parse("Add login"), repo) == EXIT_BLOCKED

    stored = OrchestratorStore(repo).load(record.id)
    assert render_report(repo, stored) in capsys.readouterr().out


def test_failed_prints_report_and_error(repo, capsys, monkeypatch):
    record = make_orchestrator(
        repo,
        phase="failed",
        tasks=two_tasks(),
        error={"phase": "delivering", "message": "push rejected"},
    )
    monkeypatch.setattr(
        orchestrate_cli, "start_orchestrator", fake_start(record.id, 1, [])
    )

    assert orchestrate_command(parse("Add login"), repo) == 1

    assert "Error: push rejected" in capsys.readouterr().err


def test_dry_run_prints_the_graph_and_resume_hint(repo, capsys, monkeypatch):
    record = make_orchestrator(
        repo,
        phase="decomposing",
        tasks=[OrchestratorTask("api", "Build the API")],
        intake={"attempts": 1, "jira_keys": [], "dry_run": True},
    )
    monkeypatch.setattr(
        orchestrate_cli, "start_orchestrator", fake_start(record.id, 0, [])
    )

    assert orchestrate_command(parse("Add login", "--dry-run"), repo) == 0

    out = capsys.readouterr().out
    assert "Dry run" in out
    assert render_status(OrchestratorStore(repo).load(record.id)) in out
    assert f"meow orchestrate resume {record.id}" in out


def test_status_prints_the_rendering(repo, capsys):
    record = make_orchestrator(repo, tasks=two_tasks())

    assert orchestrate_command(parse("status", record.id), repo) == 0

    assert capsys.readouterr().out == render_status(record) + "\n"


def test_status_without_id_uses_the_latest(repo, capsys):
    record = make_orchestrator(repo, tasks=two_tasks())

    assert orchestrate_command(parse("status"), repo) == 0

    assert capsys.readouterr().out == render_status(record) + "\n"


def test_status_of_unknown_orchestrator_fails(repo, capsys):
    assert orchestrate_command(parse("status", "0123456789ab"), repo) == 1

    assert "0123456789ab" in capsys.readouterr().err


def test_resume_passes_task_and_request(repo, monkeypatch):
    record = delivered(repo)
    seen = {}

    async def resume(repo_dir, orch_id, **kwargs):
        await asyncio.sleep(0)
        seen.update(kwargs, id=orch_id)
        return 0

    monkeypatch.setattr(orchestrate_cli, "resume_orchestrator", resume)

    code = orchestrate_command(
        parse("resume", record.id, "--task", "api", "--request", "Do it"), repo
    )

    assert code == 0
    assert seen == {"id": record.id, "task_id": "api", "request": "Do it"}


def test_cancel_reports_the_phase(repo, capsys, monkeypatch):
    record = make_orchestrator(repo, phase="cancelled")
    monkeypatch.setattr(
        orchestrate_cli,
        "cancel_orchestrator",
        lambda repo_dir, orch_id: OrchestratorStore(repo_dir).load(orch_id),
    )

    assert orchestrate_command(parse("cancel", record.id), repo) == 0

    out = capsys.readouterr().out
    assert "Cancellation requested" in out
    assert "cancelled" in out


def test_interrupt_exits_130(repo, monkeypatch):
    async def interrupted(repo_dir, intake, **kwargs):
        await asyncio.sleep(0)
        raise KeyboardInterrupt

    monkeypatch.setattr(orchestrate_cli, "start_orchestrator", interrupted)

    with pytest.raises(SystemExit) as info:
        orchestrate_command(parse("Add login"), repo)

    assert info.value.code == EXIT_CANCELLED


def test_cli_main_dispatches_orchestrate(repo, monkeypatch):
    monkeypatch.setattr(
        "meow.cli.cli.orchestrate_command", lambda args, wd: SENTINEL_EXIT
    )

    with pytest.raises(SystemExit) as info:
        cli_main(["orchestrate", "Add login", "--work-dir", str(repo)])

    assert info.value.code == SENTINEL_EXIT


def test_cli_main_reports_expected_failures(repo, capsys, monkeypatch):
    def boom(args, working_dir):
        raise ValueError("HEAD is detached")

    monkeypatch.setattr("meow.cli.cli.orchestrate_command", boom)

    with pytest.raises(SystemExit) as info:
        cli_main(["orchestrate", "Add login", "--work-dir", str(repo)])

    assert info.value.code == 1
    assert "HEAD is detached" in capsys.readouterr().err


def test_o_is_the_shortcut():
    args = _build_arg_parser().parse_args(["o", "Add login"])

    assert args.command == "orchestrate"
    assert args.words == ["Add login"]


def test_worktree_clean_dispatches_orchestrator_ids(repo, capsys, monkeypatch):
    monkeypatch.setattr(
        "meow.cli.cli.clean_orchestrator",
        lambda repo_dir, orch_id: [Path("/tmp/one")],
    )

    cli_main(["worktree", "clean", "0123456789ab", "--work-dir", str(repo)])

    assert json.loads(capsys.readouterr().out) == {
        "orchestrator": "0123456789ab",
        "removed": ["/tmp/one"],
    }


def test_worktree_clean_reports_refusals(repo, capsys, monkeypatch):
    from meow.infrastructure.worktree_controls import WorktreeSafetyError

    def refuse(repo_dir, orch_id):
        raise WorktreeSafetyError("Refusing cleanup: not delivered")

    monkeypatch.setattr("meow.cli.cli.clean_orchestrator", refuse)

    with pytest.raises(SystemExit) as info:
        cli_main(["worktree", "clean", "0123456789ab", "--work-dir", str(repo)])

    assert info.value.code == 1
    assert "Worktree clean failed: Refusing cleanup" in capsys.readouterr().err
