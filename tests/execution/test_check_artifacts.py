"""Files that checks and testers generate must not invalidate a passed review."""

import asyncio
import subprocess
import sys
from types import SimpleNamespace

import pytest

from meow.execution.run_state import RunStore
from meow.execution.workflow.builtin_steps import checks
from meow.infrastructure.checks import (
    Check,
    adopt_stage_artifacts,
    code_revision,
    reusable_results,
    run_final_checks,
    stage_baseline,
)
from meow.project.config import load_config


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _state(tmp_path, build_code):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow" / "config.toml").write_text("", encoding="utf-8")
    (tmp_path / "source.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "source.py")
    _git(tmp_path, "commit", "-qm", "init")
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt", request="x", repo=tmp_path, worktree=tmp_path, branch="main"
    )
    store.update_results(
        record.id, reviewer="PASS", reviewer_revision=code_revision(tmp_path)
    )
    config = load_config(tmp_path)
    config["build"] = [Check("build", "build", sys.executable, ("-c", build_code))]
    state = SimpleNamespace(
        store=store,
        run_id=record.id,
        checks_dir=tmp_path,
        sprint=SimpleNamespace(
            config=config, active_working_dir=lambda: tmp_path, use_worktree=False
        ),
        checks_results=None,
        checks_baseline=None,
        has_review=True,
        test=False,
        journal=lambda phase, **patch: store.transition(record.id, phase, **patch),
        block_delivery=lambda: None,
    )
    return state, store, record.id


def test_check_that_writes_an_untracked_report_keeps_the_review_valid(tmp_path):
    state, _, _ = _state(tmp_path, "open('report.xml', 'w').write('<ok/>')")
    asyncio.run(checks(state))  # must not raise "evidence failed"
    assert state.checks_results and all(r.passed for r in state.checks_results)


def test_check_that_edits_reviewed_source_still_invalidates_the_review(tmp_path):
    state, _, _ = _state(tmp_path, "open('source.py', 'w').write('x = 2\\n')")
    with pytest.raises(RuntimeError, match="evidence"):
        asyncio.run(checks(state))


@pytest.mark.parametrize("action", ["edit", "delete"])
def test_existing_untracked_source_changes_invalidate_review(tmp_path, action):
    _, store, run_id = _state(tmp_path, "pass")
    source = tmp_path / "feature.py"
    source.write_text("x = 1\n", encoding="utf-8")
    reviewed = code_revision(tmp_path)
    store.update_results(run_id, reviewer_revision=reviewed)
    baseline = stage_baseline(tmp_path)
    if action == "delete":
        source.unlink()
    else:
        source.write_text("x = 2\n", encoding="utf-8")

    adopt_stage_artifacts(store, run_id, tmp_path, baseline)

    assert store.load(run_id).results["reviewer_revision"] == reviewed
    assert code_revision(tmp_path) != reviewed


def test_checks_rerun_after_editing_existing_untracked_source(tmp_path):
    code = (
        "from pathlib import Path; p=Path('feature.py'); "
        "s=p.read_text(); p.write_text('x = 2\\n'); "
        "raise SystemExit(0 if s == 'x = 1\\n' else 1)"
    )
    state, _, _ = _state(tmp_path, code)
    (tmp_path / "feature.py").write_text("x = 1\n", encoding="utf-8")

    results = asyncio.run(run_final_checks(tmp_path, state.sprint.config))

    assert not results[0].passed


@pytest.mark.parametrize("action", ["add", "edit", "delete"])
def test_shared_meow_definitions_change_code_revision(tmp_path, action):
    _git(tmp_path, "init", "-q")
    path = tmp_path / ".meow" / "skills" / "demo" / "SKILL.md"
    path.parent.mkdir(parents=True)
    if action != "add":
        path.write_text("before", encoding="utf-8")
        _git(tmp_path, "add", "-A")
    before = code_revision(tmp_path)
    if action == "delete":
        path.unlink()
    else:
        path.write_text("after", encoding="utf-8")

    assert code_revision(tmp_path) != before


def test_meow_runtime_writes_do_not_change_code_revision(tmp_path):
    _git(tmp_path, "init", "-q")
    before = code_revision(tmp_path)
    path = tmp_path / ".meow" / "runs" / "run.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}", encoding="utf-8")

    assert code_revision(tmp_path) == before


def test_cache_directories_do_not_change_the_code_revision(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    before = code_revision(tmp_path)
    for name in ("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "entry").write_text("noise", encoding="utf-8")
    assert code_revision(tmp_path) == before


def test_tester_artifacts_keep_the_review_valid(tmp_path):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, patch

    from meow.execution.orchestrator import review_then_test
    from meow.execution.sprint import Sprint
    from meow.infrastructure.test_runner import VerificationStageEvidence

    _, store, run_id = _state(tmp_path, "pass")
    sprint = Sprint(
        repo_dir=tmp_path,
        config={"_run_journal": (store, run_id), "lint": [], "docs_dir": "docs"},
        explorer=None,
        lint_hook=None,
    )
    plan = tmp_path / "plan.md"

    @asynccontextmanager
    async def stage(active_dir, config):
        yield VerificationStageEvidence()

    async def tester(*_args, **_kwargs):  # ruff: ignore[unused-async]
        (tmp_path / "screenshot.png").write_bytes(b"png")
        return "PASS", "STATUS: PASS"

    with (
        patch(
            "meow.execution.orchestrator.ReviewerAgent.review_plan",
            new=AsyncMock(return_value=("PASS", "STATUS: PASS")),
        ),
        patch("meow.execution.orchestrator.prepared_test_stage", new=stage),
        patch("meow.execution.orchestrator.VerificationAgent.test_plan", new=tester),
    ):
        result = asyncio.run(review_then_test(sprint, plan, 1))

    assert result.status == "PASS"
    saved = store.load(run_id).results["reviewer_revision"]
    assert saved == code_revision(tmp_path)


def _verified(tmp_path):
    state, _, _ = _state(tmp_path, "pass")
    (tmp_path / "feature.py").write_text("x = 1\n", encoding="utf-8")
    config = state.sprint.config
    results = asyncio.run(run_final_checks(tmp_path, config))
    return config, results, stage_baseline(tmp_path)


def test_results_are_reused_when_a_stage_only_adds_files(tmp_path):
    config, results, baseline = _verified(tmp_path)
    (tmp_path / "review.md").write_text("report", encoding="utf-8")

    reused = asyncio.run(reusable_results(tmp_path, config, results, baseline))

    assert reused and all(r.revision == code_revision(tmp_path) for r in reused)


@pytest.mark.parametrize("action", ["edit", "delete"])
def test_results_are_not_reused_after_source_changes(tmp_path, action):
    config, results, baseline = _verified(tmp_path)
    source = tmp_path / "feature.py"
    if action == "delete":
        source.unlink()
    else:
        source.write_text("x = 2\n", encoding="utf-8")

    assert asyncio.run(reusable_results(tmp_path, config, results, baseline)) is None


def test_results_are_not_reused_without_a_baseline(tmp_path):
    config, results, _ = _verified(tmp_path)
    assert asyncio.run(reusable_results(tmp_path, config, results, None)) is None


def test_verify_fails_the_loop_gate_with_failing_output(tmp_path):
    from meow.execution.workflow.builtin_steps import verify
    from meow.project.custom.workflows import Step

    state, _, _ = _state(tmp_path, "print('boom'); raise SystemExit(3)")
    gates = []
    state.gate_result = lambda step, status, verdict: gates.append((status, verdict))
    step = Step(kind="uses", id="verify", target="builtin.verify")
    assert asyncio.run(verify(state, step)) == "FAIL"
    status, verdict = gates[0]
    assert status == "FAIL" and "exited 3" in verdict and "boom" in verdict
    assert state.checks_baseline is not None
