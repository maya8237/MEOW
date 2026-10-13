"""Recording failed attempts and applying triage decisions to the task graph."""

import json
import re
import subprocess
from pathlib import Path

import pytest

from meow.execution.orchestration.model import OrchestratorRecord, OrchestratorTask
from meow.execution.orchestration.store import OrchestratorStore
from meow.execution.orchestration.triage import (
    FAILURE_CAUSES,
    TriageDecision,
    append_triage_notes,
    apply_decision,
    apply_decision_document,
    apply_decision_file,
    decision_file,
    parse_decision,
    record_failure,
)
from meow.execution.run_state import RunStore

MAX_CYCLES = 3
SECOND_CYCLE = 2
MERGE_PARENTS = 2
PREFIX = "orchestrator/0123456789ab"


def git(directory: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=directory, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def make_record(repo: Path, tasks: list[OrchestratorTask]) -> OrchestratorRecord:
    record = OrchestratorStore(repo).create(
        objective="objective",
        source="objective",
        base_branch="main",
        mode="cli",
        max_parallel=3,
        options={"test": False, "pr": True},
    )
    record.phase = "running"
    record.tasks = list(tasks)
    return record


def task(
    task_id: str,
    *,
    status: str = "failed",
    depends_on: tuple[str, ...] = (),
    cycles: int = 0,
) -> OrchestratorTask:
    return OrchestratorTask(
        id=task_id,
        request=f"do {task_id}",
        depends_on=list(depends_on),
        status=status,
        cycles=cycles,
    )


def decide(repo: Path, record: OrchestratorRecord, task_id: str, **fields) -> list[str]:
    decision = parse_decision({"rationale": "because", **fields})
    return apply_decision(repo, record, task_id, decision, max_cycles=MAX_CYCLES)


def attempt_with_plan(repo: Path, plan: Path | None) -> str:
    """A finished child run whose journal names `plan`; returns its id."""
    store = RunStore(repo)
    run = store.create(
        source="orchestrator",
        request="do api",
        repo=repo,
        worktree=repo,
        branch="orchestrator/x",
    )
    if plan is None:
        store.transition(run.id, "failed")
    else:
        store.transition(run.id, "failed", plan_file=str(plan))
    return run.id


def failed_with_plan(tmp_path: Path, *, plan_text: str = "# Plan\n"):
    """A record whose failed `api` task has a plan file from its last attempt."""
    plan = tmp_path / "plan.md"
    plan.write_text(plan_text, encoding="utf-8")
    record = make_record(tmp_path, [task("api")])
    record.task("api").run_ids = [attempt_with_plan(tmp_path, plan)]
    return record, plan


def test_retry_increments_cycles_and_requeues(tmp_path):
    record = make_record(tmp_path, [task("api")])

    assert decide(tmp_path, record, "api", action="retry") == ["api"]

    api = record.task("api")
    assert (api.status, api.cycles) == ("pending", 1)
    assert api.next_attempt == {"resume_at": "generate", "plan_file": None}
    assert api.history[-1]["action"] == "retry"
    assert api.history[-1]["rationale"] == "because"
    assert record.phase == "running"


def test_retry_with_a_plan_continues_at_review(tmp_path):
    record, plan = failed_with_plan(tmp_path)

    decide(tmp_path, record, "api", action="retry")

    assert record.task("api").next_attempt == {
        "resume_at": "review",
        "plan_file": str(plan),
    }


def test_retry_ignores_a_plan_file_that_is_gone(tmp_path):
    record, plan = failed_with_plan(tmp_path)
    plan.unlink()

    decide(tmp_path, record, "api", action="retry")

    assert record.task("api").next_attempt == {
        "resume_at": "generate",
        "plan_file": None,
    }


def test_resume_appends_notes_and_resumes_at_review(tmp_path):
    record, plan = failed_with_plan(tmp_path)

    decide(tmp_path, record, "api", action="resume", notes="Fix test_login in api.py")

    text = plan.read_text(encoding="utf-8")
    assert text.startswith("# Plan\n")
    assert "## Orchestrator triage notes (attempt 1)" in text
    assert "Fix test_login in api.py" in text
    api = record.task("api")
    assert api.next_attempt == {"resume_at": "review", "plan_file": str(plan)}
    assert (api.status, api.cycles) == ("pending", 1)


def test_second_resume_numbers_its_notes_by_attempt(tmp_path):
    record, plan = failed_with_plan(tmp_path)
    decide(tmp_path, record, "api", action="resume", notes="first note")
    record.task("api").status = "failed"

    decide(tmp_path, record, "api", action="resume", notes="second note")

    text = plan.read_text(encoding="utf-8")
    assert "## Orchestrator triage notes (attempt 2)" in text
    assert record.task("api").cycles == SECOND_CYCLE


def test_resume_without_notes_writes_the_rationale(tmp_path):
    record, plan = failed_with_plan(tmp_path)

    decide(tmp_path, record, "api", action="resume")

    assert "because" in plan.read_text(encoding="utf-8")


def test_resume_without_a_plan_blocks_and_keeps_cycles(tmp_path):
    record = make_record(tmp_path, [task("api")])

    assert decide(tmp_path, record, "api", action="resume") == []

    api = record.task("api")
    assert (api.status, api.cycles) == ("blocked", 0)
    assert record.phase == "blocked"
    assert record.blocker["cause"] == "invalid-decision"
    assert "plan file" in record.blocker["needed"]


def test_rescope_rewrite_replaces_request(tmp_path):
    record = make_record(tmp_path, [task("api")])

    decide(tmp_path, record, "api", action="rescope", request="Add the API v2 only")

    api = record.task("api")
    assert api.request == "Add the API v2 only"
    assert (api.status, api.cycles) == ("pending", 1)
    assert api.next_attempt == {"resume_at": "generate", "plan_file": None}


def test_rescope_to_the_same_request_twice_is_a_repeat(tmp_path):
    record = make_record(tmp_path, [task("api")])
    decide(tmp_path, record, "api", action="rescope", request="Narrower api")
    record.task("api").status = "failed"

    assert (
        decide(tmp_path, record, "api", action="rescope", request="Narrower api") == []
    )

    assert record.blocker["cause"] == "repeated-decision"
    assert record.task("api").cycles == 1


def test_split_inherits_parent_cycles_and_dependencies(tmp_path):
    base = task("base", status="merged")
    record = make_record(tmp_path, [base, task("api", depends_on=("base",), cycles=2)])

    pending = decide(
        tmp_path,
        record,
        "api",
        action="rescope",
        subtasks=[
            {"id": "api-a", "request": "part a", "expected_paths": ["src/a.py"]},
            {"id": "api-b", "request": "part b", "depends_on": ["api-a"]},
        ],
    )

    assert pending == ["api-a", "api-b"]
    assert [t.id for t in record.tasks] == ["base", "api-a", "api-b"]
    first, second = record.task("api-a"), record.task("api-b")
    assert (first.cycles, second.cycles) == (3, 3)
    assert (first.status, second.status) == ("pending", "pending")
    assert first.depends_on == ["base"]
    assert second.depends_on == ["base", "api-a"]
    assert first.expected_paths == ["src/a.py"]
    assert first.history[0]["action"] == "split"
    assert first.history[0]["parent"] == "api"


def test_split_rewires_dependents_to_all_subtasks(tmp_path):
    record = make_record(
        tmp_path,
        [
            task("api"),
            task("ui", status="pending", depends_on=("api",)),
            task("docs", status="pending", depends_on=("x", "api", "y")),
            task("x", status="merged"),
            task("y", status="merged"),
        ],
    )

    decide(
        tmp_path,
        record,
        "api",
        action="rescope",
        subtasks=[
            {"id": "api-a", "request": "part a"},
            {"id": "api-b", "request": "part b"},
        ],
    )

    assert record.task("ui").depends_on == ["api-a", "api-b"]
    assert record.task("docs").depends_on == ["x", "api-a", "api-b", "y"]
    assert [t.id for t in record.tasks][:3] == ["api-a", "api-b", "ui"]


def test_split_may_reuse_the_parents_own_id(tmp_path):
    record = make_record(tmp_path, [task("api")])

    pending = decide(
        tmp_path,
        record,
        "api",
        action="rescope",
        subtasks=[
            {"id": "api", "request": "the core"},
            {"id": "api-extra", "request": "the rest"},
        ],
    )

    assert pending == ["api", "api-extra"]
    assert record.task("api").request == "the core"


def test_split_with_colliding_id_blocks_task(tmp_path):
    record = make_record(
        tmp_path, [task("api"), task("ui", status="pending", depends_on=("api",))]
    )
    before = [t.id for t in record.tasks]

    pending = decide(
        tmp_path,
        record,
        "api",
        action="rescope",
        subtasks=[
            {"id": "UI", "request": "clashes with ui"},
            {"id": "api-b", "request": "fine"},
        ],
    )

    assert pending == []
    assert [t.id for t in record.tasks] == before
    assert record.task("ui").depends_on == ["api"]
    api = record.task("api")
    assert (api.status, api.cycles) == ("blocked", 0)
    assert record.phase == "blocked"
    assert record.blocker["cause"] == "invalid-decision"
    assert "duplicate task ID" in record.blocker["needed"]


def test_split_beyond_cap_blocks_task(tmp_path):
    others = [task(f"t{index}", status="merged") for index in range(30)]
    record = make_record(tmp_path, [task("api"), *others])
    before = [t.id for t in record.tasks]

    decide(
        tmp_path,
        record,
        "api",
        action="rescope",
        subtasks=[{"id": f"api-{name}", "request": "part"} for name in "abc"],
    )

    assert [t.id for t in record.tasks] == before
    assert record.blocker["cause"] == "invalid-decision"
    assert "1 to 32" in record.blocker["needed"]


def test_split_with_unknown_dependency_blocks_task(tmp_path):
    record = make_record(tmp_path, [task("api")])

    decide(
        tmp_path,
        record,
        "api",
        action="rescope",
        subtasks=[{"id": "api-a", "request": "part", "depends_on": ["ghost"]}],
    )

    assert record.blocker["cause"] == "invalid-decision"
    assert "unknown dependency" in record.blocker["needed"]
    assert [t.id for t in record.tasks] == ["api"]


def test_prerequisite_added_and_task_depends_on_it(tmp_path):
    record = make_record(tmp_path, [task("base", status="merged"), task("api")])

    pending = decide(
        tmp_path,
        record,
        "api",
        action="rescope",
        prerequisite={
            "id": "schema",
            "request": "Add the schema",
            "depends_on": ["base"],
        },
    )

    assert pending == ["schema", "api"]
    assert [t.id for t in record.tasks] == ["base", "schema", "api"]
    schema, api = record.task("schema"), record.task("api")
    assert (schema.status, schema.cycles, schema.depends_on) == ("pending", 0, ["base"])
    assert schema.history[0]["action"] == "prerequisite"
    assert api.depends_on == ["schema"]
    assert (api.status, api.cycles) == ("pending", 1)
    assert api.next_attempt == {
        "resume_at": "generate",
        "plan_file": None,
        "reintegrate": True,
    }


def test_prerequisite_that_creates_a_cycle_blocks_task(tmp_path):
    record = make_record(tmp_path, [task("api")])

    decide(
        tmp_path,
        record,
        "api",
        action="rescope",
        prerequisite={"id": "schema", "request": "Add it", "depends_on": ["api"]},
    )

    assert record.blocker["cause"] == "invalid-decision"
    assert "cycle" in record.blocker["needed"]
    assert [t.id for t in record.tasks] == ["api"]
    assert record.task("api").depends_on == []


def reintegration_case(git_repo, tmp_path, *, conflict: bool):
    """A failed `api` task whose branch trails the integration branch."""
    repo, _remote = git_repo
    main = git(repo, "branch", "--show-current")
    integration = tmp_path / "integration"
    git(repo, "worktree", "add", "-b", PREFIX, str(integration), main)
    (integration / ("file.txt" if conflict else "integration.txt")).write_text(
        "from integration\n", encoding="utf-8"
    )
    git(integration, "add", "-A")
    git(integration, "commit", "-qm", "integration work")
    worktree = tmp_path / "task-api"
    git(repo, "worktree", "add", "-b", f"{PREFIX}-api", str(worktree), main)
    (worktree / ("file.txt" if conflict else "api.txt")).write_text(
        "from the task\n", encoding="utf-8"
    )
    git(worktree, "add", "-A")
    git(worktree, "commit", "-qm", "task work")
    plan = tmp_path / "plan.md"
    plan.write_text("# Plan\n", encoding="utf-8")
    record = make_record(repo, [task("api")])
    record.integration["branch"] = PREFIX
    api = record.task("api")
    api.worktree, api.branch = str(worktree), f"{PREFIX}-api"
    api.run_ids = [attempt_with_plan(repo, plan)]
    return repo, record, worktree, plan


def test_reintegrate_merges_integration_tip_into_task_branch(git_repo, tmp_path):
    repo, record, worktree, plan = reintegration_case(
        git_repo, tmp_path, conflict=False
    )

    decide(repo, record, "api", action="reintegrate", notes="Re-check against main")

    assert (worktree / "integration.txt").is_file()
    assert (worktree / "api.txt").is_file()
    parents = git(worktree, "log", "-1", "--format=%P").split()
    assert len(parents) == MERGE_PARENTS
    api = record.task("api")
    assert (api.status, api.cycles) == ("pending", 1)
    assert api.next_attempt == {"resume_at": "review", "plan_file": str(plan)}
    assert "Re-check against main" in plan.read_text(encoding="utf-8")


def test_reintegrate_conflict_blocks(git_repo, tmp_path):
    repo, record, worktree, plan = reintegration_case(git_repo, tmp_path, conflict=True)
    task_tip = git(worktree, "rev-parse", "HEAD")

    assert decide(repo, record, "api", action="reintegrate") == []

    assert record.blocker["cause"] == "reintegrate-conflict"
    assert record.phase == "blocked"
    api = record.task("api")
    assert (api.status, api.cycles) == ("blocked", 0)
    assert git(worktree, "rev-parse", "HEAD") == task_tip
    assert git(worktree, "status", "--porcelain") == ""
    assert plan.read_text(encoding="utf-8") == "# Plan\n"


def test_reintegrate_without_integration_branch_blocks(git_repo, tmp_path):
    repo, record, _worktree, _plan = reintegration_case(
        git_repo, tmp_path, conflict=False
    )
    record.integration["branch"] = ""

    decide(repo, record, "api", action="reintegrate")

    assert record.blocker["cause"] == "invalid-decision"
    assert "integration branch" in record.blocker["needed"]


def test_record_failure_requests_triage_and_marks_task_failed(tmp_path):
    record = make_record(tmp_path, [task("api", status="running")])

    result = record_failure(
        record,
        "api",
        cause="child-failed",
        evidence=["run.json"],
        max_cycles=MAX_CYCLES,
    )

    assert result == "triage"
    api = record.task("api")
    assert api.status == "failed"
    assert api.history[-1]["action"] == "failed"
    assert api.history[-1]["cause"] == "child-failed"
    assert api.history[-1]["evidence"] == ["run.json"]
    assert record.phase == "running"


def test_failure_at_max_cycles_blocks_recovery_exhausted(tmp_path):
    record = make_record(tmp_path, [task("api", status="running", cycles=MAX_CYCLES)])

    result = record_failure(
        record,
        "api",
        cause="child-failed",
        evidence=["run.json"],
        max_cycles=MAX_CYCLES,
    )

    assert result == "blocked"
    assert record.phase == "blocked"
    assert record.task("api").status == "blocked"
    assert record.blocker["cause"] == "recovery-exhausted"
    assert record.blocker["task_id"] == "api"
    assert record.blocker["evidence"] == ["run.json"]
    assert "3 recovery cycles" in record.blocker["needed"]


def test_failure_while_blocked_is_recorded_without_triage(tmp_path):
    record = make_record(
        tmp_path, [task("api", status="blocked"), task("ui", status="running")]
    )
    record.phase = "blocked"
    record.blocker = {
        "task_id": "api",
        "cause": "needs-human",
        "evidence": [],
        "needed": "x",
    }

    result = record_failure(
        record, "ui", cause="worker-died", evidence=[], max_cycles=MAX_CYCLES
    )

    assert result == "recorded"
    assert record.task("ui").status == "failed"
    assert record.blocker["task_id"] == "api"


@pytest.mark.parametrize("cause", FAILURE_CAUSES)
def test_record_failure_accepts_every_failure_cause(tmp_path, cause):
    record = make_record(tmp_path, [task("api", status="running")])

    assert (
        record_failure(record, "api", cause=cause, evidence=[], max_cycles=MAX_CYCLES)
        == "triage"
    )


def test_record_failure_rejects_unknown_cause(tmp_path):
    record = make_record(tmp_path, [task("api", status="running")])

    with pytest.raises(ValueError, match="Unknown failure cause 'boom'"):
        record_failure(record, "api", cause="boom", evidence=[], max_cycles=MAX_CYCLES)


def test_identical_decision_with_unchanged_request_blocks(tmp_path):
    record = make_record(tmp_path, [task("api")])
    decide(tmp_path, record, "api", action="retry")
    record.task("api").status = "failed"

    assert decide(tmp_path, record, "api", action="retry") == []

    api = record.task("api")
    assert (api.status, api.cycles) == ("blocked", 1)
    assert record.blocker["cause"] == "repeated-decision"


def test_a_different_decision_after_a_retry_is_applied(tmp_path):
    record = make_record(tmp_path, [task("api")])
    decide(tmp_path, record, "api", action="retry")
    record.task("api").status = "failed"

    decide(tmp_path, record, "api", action="rescope", request="A narrower api task")

    assert record.task("api").cycles == SECOND_CYCLE
    assert record.blocker is None


def test_resumes_with_different_notes_are_not_repeats(tmp_path):
    record, _plan = failed_with_plan(tmp_path)
    decide(tmp_path, record, "api", action="resume", notes="one")
    record.task("api").status = "failed"

    decide(tmp_path, record, "api", action="resume", notes="two")

    assert record.task("api").cycles == SECOND_CYCLE
    assert record.blocker is None


def test_blocked_decision_sets_blocker_needed(tmp_path):
    record = make_record(tmp_path, [task("api", status="running")])
    record_failure(
        record,
        "api",
        cause="child-failed",
        evidence=["run.json"],
        max_cycles=MAX_CYCLES,
    )

    pending = decide(
        tmp_path, record, "api", action="blocked", needed="Staging credentials"
    )

    assert pending == []
    assert record.phase == "blocked"
    assert record.task("api").status == "blocked"
    assert record.task("api").cycles == 0
    assert record.blocker == {
        "task_id": "api",
        "cause": "needs-human",
        "evidence": ["run.json"],
        "needed": "Staging credentials",
    }


def test_blocked_decision_without_needed_uses_the_rationale(tmp_path):
    record = make_record(tmp_path, [task("api")])

    decide(tmp_path, record, "api", action="blocked", rationale="Needs a human call")

    assert record.blocker["needed"] == "Needs a human call"


def test_apply_at_max_cycles_blocks_even_without_record_failure(tmp_path):
    record = make_record(tmp_path, [task("api", cycles=MAX_CYCLES)])

    assert decide(tmp_path, record, "api", action="retry") == []

    assert record.blocker["cause"] == "recovery-exhausted"
    assert record.task("api").cycles == MAX_CYCLES


def test_apply_requires_a_failed_task(tmp_path):
    record = make_record(tmp_path, [task("api", status="running")])

    with pytest.raises(ValueError, match="Task api is running, not failed"):
        decide(tmp_path, record, "api", action="retry")


def test_the_first_blocker_is_kept(tmp_path):
    record = make_record(tmp_path, [task("api"), task("ui")])

    decide(tmp_path, record, "api", action="blocked", needed="first")
    decide(tmp_path, record, "ui", action="blocked", needed="second")

    assert record.blocker["task_id"] == "api"
    assert record.task("ui").status == "blocked"


def test_invalid_decision_json_blocks(tmp_path):
    record = make_record(tmp_path, [task("api")])

    pending = apply_decision_document(
        tmp_path,
        record,
        "api",
        {"action": "explode", "rationale": "x"},
        max_cycles=MAX_CYCLES,
    )

    assert pending == []
    assert record.blocker["cause"] == "invalid-decision"
    assert "Unknown action 'explode'" in record.blocker["needed"]
    assert record.task("api").status == "blocked"


def test_decision_file_applies_a_valid_document(tmp_path):
    record = make_record(tmp_path, [task("api")])
    path = tmp_path / "decision.json"
    path.write_text(
        json.dumps({"action": "retry", "rationale": "flaky port"}), encoding="utf-8"
    )

    pending = apply_decision_file(tmp_path, record, "api", path, max_cycles=MAX_CYCLES)

    assert pending == ["api"]
    assert record.task("api").cycles == 1


def test_missing_decision_file_blocks(tmp_path):
    record = make_record(tmp_path, [task("api")])
    path = tmp_path / "decision.json"

    apply_decision_file(tmp_path, record, "api", path, max_cycles=MAX_CYCLES)

    assert record.blocker["cause"] == "invalid-decision"
    assert (
        f"Triage decision file {path} is missing or unreadable"
        == (record.blocker["needed"])
    )


def test_decision_file_holding_null_blocks(tmp_path):
    record = make_record(tmp_path, [task("api")])
    path = tmp_path / "decision.json"
    path.write_text("null", encoding="utf-8")

    apply_decision_file(tmp_path, record, "api", path, max_cycles=MAX_CYCLES)

    assert record.blocker["cause"] == "invalid-decision"
    assert record.blocker["needed"] == "Triage decision must be a JSON object"


def test_malformed_decision_file_blocks(tmp_path):
    record = make_record(tmp_path, [task("api")])
    path = tmp_path / "decision.json"
    path.write_text("{not json", encoding="utf-8")

    apply_decision_file(tmp_path, record, "api", path, max_cycles=MAX_CYCLES)

    assert record.blocker["cause"] == "invalid-decision"
    assert "is not valid JSON" in record.blocker["needed"]


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ([], "Triage decision must be a JSON object"),
        ({"rationale": "x"}, "Unknown action None"),
        ({"action": "retry"}, "rationale must be a non-empty string"),
        ({"action": "retry", "rationale": " "}, "rationale must be a non-empty"),
        ({"action": "retry", "rationale": "x", "extra": 1}, "Unknown decision key"),
        ({"action": "retry", "rationale": "x", "notes": 5}, "notes must be a string"),
        (
            {"action": "retry", "rationale": "x", "request": "y"},
            "retry takes no request, subtasks, or prerequisite",
        ),
        (
            {"action": "rescope", "rationale": "x"},
            "rescope needs exactly one of request, subtasks, prerequisite",
        ),
        (
            {
                "action": "rescope",
                "rationale": "x",
                "request": "y",
                "subtasks": [{"id": "a", "request": "b"}],
            },
            "rescope needs exactly one of request, subtasks, prerequisite",
        ),
        (
            {"action": "rescope", "rationale": "x", "subtasks": []},
            "subtasks must be a non-empty list",
        ),
        (
            {"action": "rescope", "rationale": "x", "subtasks": ["a"]},
            "subtask must be an object",
        ),
        (
            {"action": "rescope", "rationale": "x", "subtasks": [{"id": "a"}]},
            "subtask request must be a non-empty string",
        ),
        (
            {
                "action": "rescope",
                "rationale": "x",
                "subtasks": [{"id": "a", "request": "b", "owner": "me"}],
            },
            "subtask has unknown key(s): owner",
        ),
        (
            {
                "action": "rescope",
                "rationale": "x",
                "prerequisite": {"id": "a", "request": "b", "depends_on": "c"},
            },
            "prerequisite depends_on must be a list of non-empty strings",
        ),
    ],
)
def test_parse_decision_rejects(raw, message):
    with pytest.raises(ValueError, match=re.escape(message)):
        parse_decision(raw)


def test_parse_decision_normalises_valid_documents():
    decision = parse_decision({
        "action": "rescope",
        "rationale": " split it ",
        "subtasks": [{"id": "a", "request": "b"}],
    })

    assert decision == TriageDecision(
        action="rescope",
        rationale="split it",
        subtasks=({"id": "a", "request": "b", "depends_on": [], "expected_paths": []},),
    )


def test_decision_key_ignores_rationale_but_not_notes():
    plain = parse_decision({"action": "resume", "rationale": "one", "notes": "n"})
    reworded = parse_decision({"action": "resume", "rationale": "two", "notes": "n"})
    renoted = parse_decision({"action": "resume", "rationale": "one", "notes": "m"})

    assert plain.key() == reworded.key()
    assert plain.key() != renoted.key()


def test_decision_file_name_uses_the_next_cycle(tmp_path):
    assert decision_file(tmp_path, task("api", cycles=2)) == (
        tmp_path / "decision-api-3.json"
    )


def test_append_triage_notes_format(tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text("# Plan", encoding="utf-8")

    append_triage_notes(plan, 2, "  Fix it.  ")

    assert plan.read_text(encoding="utf-8") == (
        "# Plan\n\n## Orchestrator triage notes (attempt 2)\n\nFix it.\n"
    )
