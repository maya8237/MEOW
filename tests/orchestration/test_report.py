"""The report, status text, rollup line, and exit codes."""

import pytest

from meow.execution.orchestration.model import OrchestratorTask
from meow.execution.orchestration.report import (
    exit_code,
    render_report,
    render_status,
    rollup_line,
    write_report,
)
from meow.execution.orchestration.store import OrchestratorStore
from meow.execution.run_state import RunStore


def make_record(tmp_path, tasks, **changes):
    store = OrchestratorStore(tmp_path)
    record = store.create(
        objective="Add login\nwith SSO",
        source="objective",
        base_branch="main",
        mode="cli",
        max_parallel=3,
        options={"test": False, "pr": True},
    )

    def mutate(item):
        item.tasks = tasks
        item.integration["branch"] = f"orchestrator/{item.id}"
        for key, value in changes.items():
            setattr(item, key, value)

    store.update(record.id, mutate)
    return store, store.load(record.id)


def child_run(tmp_path, tokens=None, cost=None):
    runs = RunStore(tmp_path)
    run = runs.create(
        source="orchestrator",
        request="r",
        repo=tmp_path,
        worktree=tmp_path,
        branch="b",
    )
    if tokens is not None:
        runs.add_usage(
            run.id,
            {
                "role": "generator",
                "turns": 2,
                "duration_ms": 10,
                "tokens": tokens,
                "cost_usd": cost,
            },
        )
    return run.id


def test_blocked_report_leads_with_blocker_and_resume_commands(tmp_path):
    blocker = {
        "task_id": "api",
        "cause": "needs-human",
        "evidence": ["/tmp/a.json", "/tmp/b.log"],
        "needed": "Provide the staging credentials",
    }
    tasks = [OrchestratorTask("api", "Build", status="blocked")]
    _store, record = make_record(tmp_path, tasks, phase="blocked", blocker=blocker)

    text = render_report(tmp_path, record)

    assert text.startswith(f"# Orchestrator {record.id}: blocked\n\n## Blocker\n")
    assert text.index("## Blocker") < text.index("## Objective")
    assert "Blocked task: api" in text
    assert "Cause: needs-human" in text
    assert "Evidence: /tmp/a.json" in text
    assert "Needed: Provide the staging credentials" in text
    assert f"meow orchestrate resume {record.id}" in text
    assert f"/meow:orchestrate resume {record.id}" in text


def test_report_lists_tasks_with_keys_cycles_runs_history_and_usage(tmp_path):
    first, second = child_run(tmp_path, 1000, 0.25), child_run(tmp_path, 500, 0.5)
    history = [
        {"action": "failure", "cause": "child-failed", "rationale": ""},
        {"action": "retry", "cause": "", "rationale": "flaky port"},
        {
            "action": "conflict-resolved",
            "cause": "",
            "rationale": "Resolved conflicts in s.py",
        },
    ]
    tasks = [
        OrchestratorTask(
            "api",
            "Build",
            source_ref="PROJ-1",
            status="merged",
            cycles=1,
            branch="orchestrator/x-api",
            run_ids=[first, second],
            history=history,
        ),
        OrchestratorTask("ui", "Form", status="merged", branch="orchestrator/x-ui"),
    ]
    _store, record = make_record(tmp_path, tasks, phase="delivered")

    text = render_report(tmp_path, record)

    assert "### api (PROJ-1)" in text
    assert "- Cycles: 1" in text
    assert "- Branch: orchestrator/x-api" in text
    assert f"- Runs: {first}, {second}" in text
    assert "- Usage: tokens 1500, cost $0.75" in text
    assert "  - failure (child-failed)" in text
    assert "  - retry: flaky port" in text
    assert "### ui\n" in text
    assert "- Usage: unavailable" in text
    assert "## Conflicts resolved\n\n- api: Resolved conflicts in s.py" in text
    assert "- Total: tokens 1500, cost $0.75" in text


def test_report_without_conflicts_or_review_says_so(tmp_path):
    _store, record = make_record(
        tmp_path, [OrchestratorTask("a", "r", status="merged")]
    )

    text = render_report(tmp_path, record)

    assert "## Conflicts resolved\n\nNone." in text
    assert "- Verdict: not run" in text
    assert "- Total: unavailable" in text


def test_report_shows_review_pr_and_error_lines(tmp_path):
    delivery = {
        "pushed_commit": "abc123",
        "pr": None,
        "pr_error": "rate limited",
        "pr_manual_link": "https://github.com/o/r/compare/main...b?expand=1",
    }
    review = {"round": 2, "verdict": "PASS", "review_file": "/tmp/r.md"}
    _store, record = make_record(
        tmp_path,
        [OrchestratorTask("a", "r", status="merged")],
        phase="delivered",
        delivery=delivery,
        review=review,
    )

    text = render_report(tmp_path, record)

    assert "- Pushed commit: abc123" in text
    assert "- PR not opened: rate limited" in text
    assert (
        "- Open it manually: https://github.com/o/r/compare/main...b?expand=1" in text
    )
    assert "- Verdict: PASS (round 2)" in text
    assert "- Review file: /tmp/r.md" in text


def test_failed_report_names_the_failing_phase(tmp_path):
    error = {"phase": "delivering", "message": "git push failed: no route"}
    _store, record = make_record(
        tmp_path,
        [OrchestratorTask("a", "r", status="merged")],
        phase="failed",
        error=error,
    )

    assert "- Error in delivering: git push failed: no route" in render_report(
        tmp_path, record
    )


def test_write_report_writes_report_md_in_the_orchestrator_directory(tmp_path):
    store, record = make_record(tmp_path, [OrchestratorTask("a", "r")])

    path = write_report(tmp_path, record)

    assert path == store.dir_for(record.id) / "report.md"
    assert path.read_text(encoding="utf-8") == render_report(tmp_path, record)
    assert list(path.parent.glob("*.tmp")) == []


def test_status_and_rollup(tmp_path):
    blocker = {
        "task_id": "b",
        "cause": "needs-human",
        "evidence": [],
        "needed": "A call",
    }
    tasks = [
        OrchestratorTask(
            "a", "r", status="merged", cycles=1, branch="orchestrator/x-a"
        ),
        OrchestratorTask("b", "r", status="blocked", run_ids=["r1", "r2"]),
    ]
    _store, record = make_record(tmp_path, tasks, phase="blocked", blocker=blocker)

    status = render_status(record)

    assert rollup_line(record) == f"orchestrator {record.id} blocked 1/2 merged"
    assert f"Orchestrator {record.id}" in status
    assert "Objective: Add login" in status
    assert "Tasks: 1/2 merged" in status
    assert "  a: merged, cycles 1, branch orchestrator/x-a, run -" in status
    assert "  b: blocked, cycles 0, branch -, run r2" in status
    assert "Needed: A call" in status
    assert f"/meow:orchestrate resume {record.id}" in status


@pytest.mark.parametrize(
    ("scenario", "code"),
    [
        (("delivered", {"url": "u", "provider": "github"}, True), 0),
        (("delivered", None, False), 0),
        (("delivered", None, True), 3),
        (("blocked", None, True), 2),
        (("cancelled", None, True), 130),
        (("failed", None, True), 1),
        (("running", None, True), 1),
    ],
)
def test_exit_codes(tmp_path, scenario, code):
    phase, pr, pr_option = scenario
    delivery = {"pushed_commit": None, "pr": pr, "pr_error": None}
    _store, record = make_record(
        tmp_path,
        [OrchestratorTask("a", "r")],
        phase=phase,
        delivery=delivery,
        options={"test": False, "pr": pr_option},
    )

    assert exit_code(record) == code
