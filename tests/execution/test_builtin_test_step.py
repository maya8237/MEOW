import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meow.execution.run_state import RunStore
from meow.execution.sprint_runner import run_sprint
from meow.execution.workflow import builtin_steps
from meow.project.plan_files import stage_file

WORKFLOW = """description = "test only"

[[steps]]
id = "test"
uses = "builtin.test"
"""


def _project(tmp_path):
    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow" / "config.toml").write_text(
        '[workflow]\ndefault="test-only"', encoding="utf-8"
    )
    folder = tmp_path / ".meow" / "workflows"
    folder.mkdir()
    (folder / "test-only.toml").write_text(WORKFLOW, encoding="utf-8")
    return stage_file(tmp_path / ".meow" / "plans", "tester")


def _query(report, text):
    async def query(self, agent, prompt, *args):  # ruff: ignore[unused-async] -- SDK double
        assert "Audit API" in prompt
        report.write_text(text, encoding="utf-8")

    return query


def test_builtin_test_runs_tester_without_test_flag(tmp_path):
    report = _project(tmp_path)
    query = _query(report, "SUMMARY: Verified\nSTATUS: PASS\n\n## Test plan\n- api")
    with patch("meow.agents.tester.VerificationAgent.run_definition_query", new=query):
        asyncio.run(
            run_sprint(tmp_path, None, "Audit API", use_worktree=False, test=False)
        )
    latest = RunStore(tmp_path).latest()
    assert latest.results["tester"] == "PASS"
    assert latest.results["test"] is True
    assert latest.results["reviewer"] != "PASS"


def test_builtin_test_failure_stops_the_run_with_tester_feedback(tmp_path):
    report = _project(tmp_path)
    query = _query(report, "SUMMARY: page blank\nSTATUS: FAIL\n\n## Test plan\n- api")
    with (
        patch("meow.agents.tester.VerificationAgent.run_definition_query", new=query),
        pytest.raises(Exception) as caught,
    ):
        asyncio.run(
            run_sprint(tmp_path, None, "Audit API", use_worktree=False, test=False)
        )
    assert "test" in str(caught.value)
    assert RunStore(tmp_path).latest().results["tester"] == "FAIL"


def test_builtin_test_is_skipped_when_tests_are_force_disabled():
    tester = AsyncMock()
    state = SimpleNamespace(
        sprint=SimpleNamespace(config={"_force_disable_tests": True}),
        gate_result=MagicMock(),
    )
    step = SimpleNamespace(id="test")
    with patch.object(builtin_steps, "_tester_stage", tester):
        status = asyncio.run(builtin_steps.test_step(state, step))
    assert status == "skipped"
    tester.assert_not_awaited()
    state.gate_result.assert_not_called()


def test_review_still_runs_tester_after_passing_review(tmp_path):
    directory = tmp_path / ".meow"
    directory.mkdir()
    (directory / "config.toml").write_text(
        '[workflow]\ndefault="review-and-fix"', encoding="utf-8"
    )
    report = stage_file(tmp_path / ".meow" / "plans", "tester")
    query = _query(report, "SUMMARY: Verified\nSTATUS: PASS\n\n## Test plan\n- api")
    with (
        patch(
            "meow.execution.workflow.builtin_steps.ReviewerAgent.review_prompt",
            new=AsyncMock(return_value=("PASS", "SUMMARY: OK\nSTATUS: PASS")),
        ),
        patch("meow.agents.tester.VerificationAgent.run_definition_query", new=query),
    ):
        asyncio.run(
            run_sprint(tmp_path, None, "Audit API", use_worktree=False, test=True)
        )
    assert RunStore(tmp_path).latest().results["tester"] == "PASS"
