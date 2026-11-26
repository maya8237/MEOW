"""The runtime appended to `.js` workflows, driven by scripted agents in Node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from meow.project.custom import builtin_dir

FIX_ROUNDS = 2  # one failed review, then the fix that passes
HARNESS = Path(__file__).parent / "fixtures" / "run_workflow.mjs"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
PREPARED = {
    "error": "",
    "project_dir": "/p",
    "active_dir": "/p/.worktrees/x",
    "plan_file": "/p/plan.md",
    "review_file": "/p/plan.review.md",
    "max_rounds": 3,
    "use_worktree": True,
    "run_id": "r1",
    "checks_configured": True,
}
PASS = {"error": "", "status": "PASS", "report": "SUMMARY: ok\nSTATUS: PASS"}
FAIL = {"error": "", "status": "FAIL", "report": "SUMMARY: bad\nSTATUS: FAIL"}
BRIEF = {
    "error": "",
    "system_prompt": "s",
    "query": "q",
    "task": "t",
    "report_file": "/r",
}


def run(name, args, answers):
    script = builtin_dir() / "workflows" / f"{name}.js"
    done = subprocess.run(
        ["node", str(HARNESS), str(script), json.dumps(args), json.dumps(answers)],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return json.loads(done.stdout)


def default_answers(**overrides):
    return {
        "prepare": [PREPARED],
        "brief": [BRIEF],
        "verdict": [PASS],
        "generate": [{"error": "", "changed": True}],
        "verify": [PASS],
        "checks": [PASS],
        "finalize": [{"error": "", "complete": True, "branch": "b", "target": "main"}],
        **overrides,
    }


def test_default_runs_every_step_in_order():
    out = run("default", "Add CSV export", default_answers())

    assert out["result"]["status"] == "complete"
    assert out["result"]["branch"] == "b"
    calls = [call for call in out["calls"] if not call.startswith(("brief", "verdict"))]
    assert calls == [
        "prepare",
        "planner",
        "plan-checker",
        "generate",
        "verify",
        "review",
        "checks",
        "finalize",
    ]


def test_a_failed_review_feeds_the_next_fix_round():
    out = run("default", "x", default_answers(**{"verdict review": [FAIL, PASS]}))

    assert out["result"]["status"] == "complete"
    assert out["calls"].count("generate") == FIX_ROUNDS


def test_a_fix_that_changes_nothing_stalls():
    answers = default_answers(**{
        "verdict review": [FAIL],
        "generate": [{"error": "", "changed": True}, {"error": "", "changed": False}],
    })
    out = run("default", "x", answers)

    assert out["result"]["status"] == "stopped"
    assert "changed no code" in out["result"]["error"]


def test_stop_after_plan_waits_for_approval():
    out = run("default", {"request": "x", "stopAfterPlan": True}, default_answers())

    assert out["result"]["status"] == "awaiting_approval"
    assert "generate" not in out["calls"]


def test_a_given_plan_skips_planning():
    out = run("default", {"plan": "/p/mine.md"}, default_answers())

    assert "planner" not in out["calls"]
    assert out["result"]["plan_file"] == "/p/mine.md"


def test_a_failed_helper_stops_with_its_message():
    answers = default_answers(prepare=[{**PREPARED, "error": "dirty tree"}])

    out = run("default", "x", answers)

    assert out["result"] == {
        "status": "stopped",
        "step": "prepare",
        "error": "dirty tree",
        "run_id": None,
    }


def test_review_and_fix_reviews_first_without_a_plan():
    answers = default_answers(**{"verdict review": [FAIL, PASS]})

    out = run("review-and-fix", "Fix auth", answers)

    assert out["result"]["status"] == "complete"
    assert out["calls"].index("review") < out["calls"].index("fix")


def test_the_tester_step_asks_for_the_cli():
    out = run("test-fix", "x", default_answers(**{"verdict review": [FAIL, PASS]}))

    assert out["result"]["status"] == "stopped"
    assert "meow run --workflow test-fix" in out["result"]["error"]
