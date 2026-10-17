"""The whole-objective review loop on the integration branch."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from meow.agents.reviewer import _verdict_status
from meow.execution.orchestration import final_review
from meow.execution.orchestration.final_review import run_objective_review
from meow.execution.orchestration.model import OrchestratorTask
from meow.execution.run_state import CheckResult
from meow.project.config_models import VerificationCommand
from tests.orchestration.fakes import (
    create_orchestrator,
    git,
    make_repository,
    orchestrator_config,
)
from tests.orchestration.review_fakes import ReviewScript, fake_stage

ROUNDS = 2


def failing_check():
    return CheckResult("lint", "ruff check", True, 1, 0.1, "bad", "rev", "fp")


def setup(tmp_path, statuses, **options):
    repo, remote = make_repository(tmp_path)
    tasks = [
        OrchestratorTask("api", "Build the api", status="merged"),
        OrchestratorTask("ui", "Build the ui\nwith details", status="merged"),
    ]
    store, oid = create_orchestrator(repo, tasks)
    store.update(oid, lambda record: record.options.update(options))
    worktree = Path(store.load(oid).integration["worktree"])
    return SimpleNamespace(
        repo=repo,
        remote=remote,
        store=store,
        oid=oid,
        config=orchestrator_config(repo),
        script=ReviewScript(statuses),
        worktree=worktree,
    )


def review(case):
    return asyncio.run(
        run_objective_review(
            case.repo, case.store, case.oid, case.config, case.script.agents
        )
    )


def no_checks_and_fake_stage(monkeypatch):
    async def no_checks(worktree, config):  # ruff: ignore[unused-async]
        return []

    monkeypatch.setattr(final_review, "run_final_checks", no_checks)
    monkeypatch.setattr(final_review, "prepared_test_stage", fake_stage)


def test_pass_first_round_proceeds(tmp_path):
    case = setup(tmp_path, ["PASS"])

    result = review(case)

    record = case.store.load(case.oid)
    assert result == "PASS"
    assert record.phase == "reviewing"
    assert record.review["round"] == 1
    assert record.review["verdict"] == "PASS"
    assert record.review["review_file"].endswith("branch.00000001.review.md")
    target, branch, objective = case.script.reviews[0]
    assert (target, branch) == ("main", f"orchestrator/{case.oid}")
    assert objective.startswith("Build it")
    assert "- api: Build the api" in objective
    assert "- ui: Build the ui" in objective
    assert "with details" not in objective
    assert case.script.fixes == []


def test_reviewer_code_edits_cannot_pass_the_objective_review(tmp_path):
    case = setup(tmp_path, ["PASS"])
    case.config["orchestrator"]["review_rounds"] = 1
    factory = case.script.agents.reviewer

    def editing_reviewer(context):
        reviewer = factory(context)
        original = reviewer.review_branch

        async def run(*args, **kwargs):
            result = await original(*args, **kwargs)
            (case.worktree / "unreviewed.py").write_text("broken = True\n")
            return result

        reviewer.review_branch = run
        return reviewer

    case.script.agents.reviewer = editing_reviewer
    assert review(case) == "FAIL"
    assert case.store.load(case.oid).phase == "blocked"


def test_resumed_review_preserves_used_rounds(tmp_path):
    case = setup(tmp_path, ["PASS"])
    case.store.update(case.oid, lambda rec: rec.review.update(round=2))
    assert review(case) == "PASS"
    assert (
        case.store.load(case.oid).review["round"]
        == case.config["orchestrator"]["review_rounds"]
    )


def test_fail_then_fix_then_pass(tmp_path):
    case = setup(tmp_path, ["FAIL", "PASS"])
    worktree = case.worktree
    before = git(worktree, "rev-parse", "HEAD")

    result = review(case)

    assert result == "PASS"
    assert len(case.script.fixes) == 1
    assert "STATUS: FAIL" in case.script.fixes[0]
    assert case.store.load(case.oid).review["round"] == ROUNDS
    assert git(worktree, "rev-parse", "HEAD") != before
    assert git(worktree, "log", "-1", "--format=%s") == (
        "Orchestrator review fixes (round 1)"
    )
    assert git(worktree, "show", "--name-only", "--format=", "HEAD") == "fix.txt"


def test_fail_after_rounds_blocks_without_pushing(tmp_path):
    case = setup(tmp_path, ["FAIL"])
    case.config["orchestrator"]["review_rounds"] = ROUNDS

    result = review(case)

    record = case.store.load(case.oid)
    assert result == "FAIL"
    assert record.phase == "blocked"
    assert record.blocker["cause"] == "objective-review"
    assert record.blocker["task_id"] is None
    assert record.blocker["evidence"] == [record.review["review_file"]]
    assert "2 round(s)" in record.blocker["needed"]
    assert len(case.script.reviews) == ROUNDS
    assert len(case.script.fixes) == 1
    assert git(case.remote, "for-each-ref") == ""


def test_failing_checks_force_fail_even_when_reviewer_passes(tmp_path, monkeypatch):
    case = setup(tmp_path, ["PASS", "PASS"])
    calls = []

    async def fake_checks(worktree, config):  # ruff: ignore[unused-async]
        calls.append(worktree)
        return [failing_check()] if len(calls) == 1 else []

    monkeypatch.setattr(final_review, "run_final_checks", fake_checks)

    result = review(case)

    first = case.worktree / ".meow" / "plans" / "branch.00000001.review.md"
    assert result == "PASS"
    assert len(case.script.fixes) == 1
    assert "Required checks failed after the last change" in case.script.fixes[0]
    assert "ruff check (exit code 1)" in case.script.fixes[0]
    assert _verdict_status(first.read_text(encoding="utf-8")) == "FAIL"
    assert case.store.load(case.oid).review["round"] == ROUNDS


@pytest.mark.parametrize(
    "scenario",
    [(True, True, 1), (True, False, 0), (False, True, 0)],
    ids=["option-and-config", "no-tester-config", "no-test-option"],
)
def test_tester_runs_only_with_test_option_and_config(tmp_path, monkeypatch, scenario):
    option, configured, expected_calls = scenario
    case = setup(tmp_path, ["PASS"], test=option)
    case.config["tester"] = {
        "tests": [VerificationCommand(Path("."), "pytest", gate=False)]
        if configured
        else []
    }
    no_checks_and_fake_stage(monkeypatch)

    result = review(case)

    assert result == "PASS"
    assert len(case.script.tests) == expected_calls


def test_tester_failure_is_a_failure(tmp_path, monkeypatch):
    case = setup(tmp_path, ["PASS"], test=True)
    case.script.tester_result = ("FAIL", "login is broken")
    case.config["tester"] = {
        "tests": [VerificationCommand(Path("."), "pytest", gate=False)]
    }
    case.config["orchestrator"]["review_rounds"] = 1
    no_checks_and_fake_stage(monkeypatch)

    result = review(case)

    assert result == "FAIL"
    assert case.store.load(case.oid).blocker["cause"] == "objective-review"
    assert case.script.fixes == []
