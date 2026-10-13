"""Read-only status reports for durable runs."""

import sys

from meow.cli.cli import cli_main
from meow.cli.status_cli import status
from meow.execution.orchestration.model import OrchestratorTask
from meow.execution.orchestration.store import OrchestratorStore
from meow.execution.run_state import RunStore
from meow.infrastructure.quality import record_concerns


def test_status_latest_and_verbose(tmp_path, capsys):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt",
        request="feature",
        repo=tmp_path,
        worktree=tmp_path,
        branch="dev",
    )
    store.transition(
        record.id, "interrupted_mutation", round=2, last_failure="process stopped"
    )
    assert status(tmp_path) == 0
    concise = capsys.readouterr().out
    assert record.id in concise and "interrupted_mutation" in concise
    assert "meow resume" in concise and "dev" in concise
    assert status(tmp_path, record.id, verbose=True) == 0
    verbose = capsys.readouterr().out
    assert "Transitions" in verbose and "unavailable" in verbose


def test_status_missing_record_is_safe(tmp_path, capsys):
    assert status(tmp_path, "unknown") != 0
    assert "meow status" in capsys.readouterr().err


def test_cli_status_never_boots_repo(tmp_path, monkeypatch, capsys):
    record = RunStore(tmp_path).create(
        source="prompt", request="x", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    monkeypatch.setattr(
        sys, "argv", ["meow", "status", record.id, "--work-dir", str(tmp_path)]
    )
    monkeypatch.setattr("meow.cli.cli._boot_repo", lambda *args, **kwargs: 1 / 0)
    with __import__("pytest").raises(SystemExit) as exit_info:
        cli_main()
    assert exit_info.value.code == 0
    assert record.id in capsys.readouterr().out


def test_status_shows_only_current_run_quality_concern(tmp_path, capsys):
    (tmp_path / "module.py").write_text("duplicate check\n")
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt", request="x", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    record_concerns(
        tmp_path,
        record.id,
        [
            {
                "path": "module.py",
                "evidence": "duplicate check",
                "impact": "maintenance",
                "follow_up": "consolidate checks",
            }
        ],
    )
    assert status(tmp_path, record.id) == 0
    output = capsys.readouterr().out
    assert "Quality concern: module.py" in output
    assert "quality score" not in output.lower()
    (tmp_path / "module.py").write_text("fixed\n")
    assert status(tmp_path, record.id) == 0
    assert "Quality concern:" not in capsys.readouterr().out


def test_status_reads_persistent_concerns_from_main_repo(tmp_path, capsys):
    from meow.infrastructure.quality import record_concerns

    main = tmp_path / "main"
    active = tmp_path / "active"
    main.mkdir()
    active.mkdir()
    (active / "module.py").write_text("duplicate check\n")
    store = RunStore(main)
    record = store.create(
        source="prompt", request="x", repo=main, worktree=active, branch="feature"
    )
    record_concerns(
        main,
        record.id,
        [
            {
                "path": "module.py",
                "evidence": "duplicate check",
                "impact": "maintenance",
                "follow_up": "consolidate checks",
            }
        ],
        evidence_root=active,
    )
    assert status(main, record.id) == 0
    assert "Quality concern: module.py" in capsys.readouterr().out


def test_status_distinguishes_final_gate_outcomes(tmp_path, capsys):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt", request="x", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    store.transition(
        record.id,
        "checks_finished",
        results={
            "checks": [
                {
                    "kind": "lint",
                    "command": "ruff check",
                    "exit_code": 0,
                    "timed_out": False,
                    "required": True,
                },
                {
                    "kind": "test",
                    "command": "pytest",
                    "exit_code": 1,
                    "timed_out": False,
                    "required": True,
                },
                {
                    "kind": "build",
                    "command": "build",
                    "exit_code": None,
                    "timed_out": True,
                    "required": False,
                },
                {
                    "kind": "browser",
                    "command": "playwright",
                    "exit_code": 0,
                    "timed_out": False,
                    "required": True,
                },
            ]
        },
    )
    assert status(tmp_path, record.id) == 0
    output = capsys.readouterr().out
    assert "Lint: PASS" in output
    assert "Test: FAIL" in output
    assert "Build: TIMEOUT (advisory)" in output
    assert "Browser: PASS" in output


def test_status_shows_task_progress_and_ownership(tmp_path, capsys):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt", request="x", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    store.transition(
        record.id,
        "implementing",
        results={
            "tasks": {
                "api": {"status": "complete", "owned_paths": ["src/api"]},
                "ui": {"status": "running", "owned_paths": ["src/ui"]},
            }
        },
    )
    assert status(tmp_path, record.id) == 0
    output = capsys.readouterr().out
    assert "Tasks: 1 complete, 1 running" in output
    assert status(tmp_path, record.id, verbose=True) == 0
    assert "src/ui" in capsys.readouterr().out


def test_status_does_not_suggest_resume_for_review_runs(tmp_path):
    from meow.cli.status_cli import recovery_command

    store = RunStore(tmp_path)
    record = store.create(
        source="review", request="r", repo=tmp_path, worktree=tmp_path, branch="b"
    )
    assert "meow resume" not in recovery_command(record)
    prompt = store.create(
        source="prompt", request="r", repo=tmp_path, worktree=tmp_path, branch="b"
    )
    assert recovery_command(prompt).startswith(f"meow resume {prompt.id}")


def test_orchestrator_child_runs_are_resumable(tmp_path):
    from meow.cli.status_cli import RESUMABLE_SOURCES, recovery_command

    record = RunStore(tmp_path).create(
        source="orchestrator",
        request="r",
        repo=tmp_path,
        worktree=tmp_path,
        branch="b",
    )

    assert "orchestrator" in RESUMABLE_SOURCES
    assert recovery_command(record).startswith(f"meow resume {record.id}")


def make_orchestrator(repo, phase, merged, total):
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
        record.tasks = [
            OrchestratorTask(
                f"t{number}",
                "request",
                status="merged" if number < merged else "pending",
            )
            for number in range(total)
        ]

    return store.update(created.id, mutate)


def test_status_lists_active_orchestrator_rollup(tmp_path, capsys):
    RunStore(tmp_path).create(
        source="prompt", request="x", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    orchestrator = make_orchestrator(tmp_path, "running", merged=1, total=2)

    assert status(tmp_path) == 0

    output = capsys.readouterr().out
    assert f"orchestrator {orchestrator.id} running 1/2 merged" in output


def test_status_with_run_id_also_lists_the_rollup(tmp_path, capsys):
    run = RunStore(tmp_path).create(
        source="prompt", request="x", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    orchestrator = make_orchestrator(tmp_path, "reviewing", merged=2, total=2)

    assert status(tmp_path, run.id) == 0

    output = capsys.readouterr().out
    assert run.id in output
    assert f"orchestrator {orchestrator.id} reviewing 2/2 merged" in output


def test_status_hides_finished_orchestrators(tmp_path, capsys):
    RunStore(tmp_path).create(
        source="prompt", request="x", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    for phase in ("delivered", "failed", "cancelled"):
        make_orchestrator(tmp_path, phase, merged=1, total=1)

    assert status(tmp_path) == 0

    assert "orchestrator " not in capsys.readouterr().out


def test_status_without_runs_still_shows_active_orchestrators(tmp_path, capsys):
    orchestrator = make_orchestrator(tmp_path, "blocked", merged=0, total=3)

    assert status(tmp_path) == 0

    assert (
        f"orchestrator {orchestrator.id} blocked 0/3 merged" in capsys.readouterr().out
    )


def test_status_of_unknown_run_still_fails_with_orchestrators_present(tmp_path, capsys):
    make_orchestrator(tmp_path, "running", merged=0, total=1)

    assert status(tmp_path, "unknown") != 0

    assert "meow status" in capsys.readouterr().err
