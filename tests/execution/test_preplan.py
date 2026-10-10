"""Behavioral tests for automatic, read-only project preparation."""

import asyncio
import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from meow.execution.run_state import RunStore
from meow.execution.sprint_runner import run_plan, run_sprint
from meow.project.preplan import (
    assess_preplan,
    gather_context,
    needs_breadboard,
    prepare_preplan,
)
from meow.project.prompts import planner_prompt


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def test_gather_context_selects_relevant_code_without_writing(tmp_path):
    _git(tmp_path, "init")
    (tmp_path / "AGENTS.md").write_text("# Agents\nUse pytest for tests.\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "payments.py").write_text(
        "def refund_payment():\n    return True\n"
    )
    (tmp_path / "src" / "colors.py").write_text("COLOR = 'red'\n")
    _git(tmp_path, "add", ".")
    before = sorted(
        str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file()
    )
    evidence = gather_context(tmp_path, "Fix refund payment")
    after = sorted(
        str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file()
    )
    assert before == after
    assert any("payments.py" in item for item in evidence.references)
    assert not any("colors.py" in item for item in evidence.references)
    assert all("create" not in item.lower() for item in evidence.uncertainty)


def test_gather_context_excludes_generated_execution_plans(tmp_path):
    _git(tmp_path, "init")
    (tmp_path / "src.py").write_text("def refund_payment(): return True\n")
    plan_dir = tmp_path / "docs" / "exec-plans" / "active"
    plan_dir.mkdir(parents=True)
    (plan_dir / "refund.md").write_text(
        "Generated execution plan for refund payment.\n"
    )
    _git(tmp_path, "add", ".")

    evidence = gather_context(tmp_path, "Fix refund payment")

    assert any("src.py" in item for item in evidence.references)
    assert not any("docs/exec-plans/" in item for item in evidence.references)


def test_missing_docs_stays_internal_uncertainty(tmp_path):
    _git(tmp_path, "init")
    (tmp_path / "src.py").write_text("def run(): pass\n")
    _git(tmp_path, "add", ".")
    evidence = gather_context(tmp_path, "Fix run")
    assert any("AGENTS.md" in item for item in evidence.uncertainty)
    assert not hasattr(evidence, "recommendations")


def test_broken_guidance_link_is_uncertainty_without_doc_recommendation(tmp_path):
    _git(tmp_path, "init")
    (tmp_path / "AGENTS.md").write_text("See [architecture](docs/missing.md).\n")
    (tmp_path / "app.py").write_text("def run(): pass\n")
    _git(tmp_path, "add", ".")
    evidence = gather_context(tmp_path, "Fix run")
    assert any(
        "AGENTS.md:1" in item and "missing.md" in item for item in evidence.uncertainty
    )
    assert all("create" not in item.lower() for item in evidence.uncertainty)


def test_clear_fix_is_direct_and_ambiguous_request_shapes(tmp_path):
    direct = assess_preplan(
        "Fix refund payment timeout", gather_context(tmp_path, "refund")
    )
    shaped = assess_preplan(
        "Improve dashboard performance across components",
        gather_context(tmp_path, "dashboard"),
    )
    assert direct.mode == "direct"
    assert shaped.mode == "shape"


def test_conflicting_product_choices_stop_without_question(tmp_path):
    result = prepare_preplan(
        tmp_path,
        "Choose between deleting accounts or retaining them forever",
        unattended=True,
    )
    assert result.decision.mode == "needs_user_decision"
    assert result.shape is None
    assert not list(tmp_path.glob("**/*.md"))


def test_unexpected_failure_before_planning_marks_the_run_failed(tmp_path):
    from meow.execution.sprint import Sprint

    sprint = Sprint(
        tmp_path, {"docs_dir": ".", "lint": [], "max_rounds": 1}, None, None
    )
    with (
        patch(
            "meow.execution.sprint_runner._prepare_sprint",
            return_value=(sprint, "x", tmp_path),
        ),
        patch(
            "meow.execution.sprint_runner.gather_context",
            side_effect=OSError("disk gone"),
        ),
        pytest.raises(OSError, match="disk gone"),
    ):
        asyncio.run(run_sprint(tmp_path, "x", "Add export", use_worktree=False))
    record = RunStore(tmp_path).latest()
    assert record.phase == "failed"
    assert record.last_failure == "disk gone"


@pytest.mark.parametrize(
    "request_text",
    [
        "Fix the crash whether the input is empty or null",
        "Accept either a path or a URL for --plan",
    ],
)
def test_case_lists_are_not_product_decisions(tmp_path, request_text):
    result = prepare_preplan(tmp_path, request_text, unattended=True)
    assert result.decision.mode != "needs_user_decision"


def test_breadboard_only_for_cross_component_ui(tmp_path):
    evidence = gather_context(tmp_path, "request")
    assert not needs_breadboard("Fix CLI help output", evidence)
    assert needs_breadboard("Build a dashboard across API and UI components", evidence)


def test_shaped_preplan_artifact_has_verified_references(tmp_path):
    _git(tmp_path, "init")
    (tmp_path / "dashboard.py").write_text("def show_dashboard(): pass\n")
    _git(tmp_path, "add", ".")
    result = prepare_preplan(
        tmp_path, "Improve dashboard performance across components"
    )
    assert result.decision.mode == "shape"
    assert result.shape is not None
    assert result.shape.options
    assert result.shape.fit_checks
    assert "dashboard.py" in json.dumps(result.to_dict())


def test_planner_prompt_receives_relevant_evidence_without_doc_advice(tmp_path):
    context = {
        "evidence": {
            "references": ["src/payments.py:2: def refund_payment()"],
            "constraints": [],
            "uncertainty": [],
        },
        "decision": {"mode": "direct", "reasons": []},
    }
    prompt = planner_prompt(tmp_path / "plan.md", project_context=context)
    assert "src/payments.py:2" in prompt
    assert "documentation" not in prompt.lower()


def test_plan_command_prepares_project_context_behind_the_scenes(tmp_path):
    from meow.execution.sprint import Sprint

    sprint = Sprint(tmp_path, {"docs_dir": ".", "lint": []}, None, None)
    plan_file = tmp_path / "plan.md"
    plan_file.write_text("# Plan\n", encoding="utf-8")
    planner = AsyncMock(return_value=plan_file)
    with (
        patch(
            "meow.execution.sprint_runner._prepare_sprint",
            return_value=(sprint, "feature", tmp_path),
        ),
        patch("meow.execution.sprint_runner.PlannerAgent") as agent,
    ):
        agent.return_value.run = planner
        asyncio.run(run_plan(tmp_path, "feature", "Fix the command"))
    assert sprint.config["_preplan_context"]["decision"]["mode"] == "direct"
    planner.assert_awaited_once()


def test_unattended_product_choice_stops_before_planning(tmp_path):
    from meow.execution.sprint import Sprint

    sprint = Sprint(
        tmp_path, {"docs_dir": ".", "lint": [], "max_rounds": 1}, None, None
    )
    with (
        patch(
            "meow.execution.sprint_runner._prepare_sprint",
            return_value=(sprint, "x", tmp_path),
        ),
        patch("meow.execution.sprint_runner.PlannerAgent") as planner,
        pytest.raises(RuntimeError, match="product decision"),
    ):
        asyncio.run(
            run_sprint(
                tmp_path,
                "x",
                "Choose between deleting all accounts or retaining them forever",
                use_worktree=False,
                unattended=True,
            )
        )
    planner.assert_not_called()
    record = RunStore(tmp_path).latest()
    assert record.phase == "needs_user_decision"
    assert "preplan" in record.results
