from meow.hooks.handlers import (
    HANDLERS,
    capture_completed_plan,
    lint_after_edit,
    shaping_ripple,
    validate_plan_stop,
)
from meow.project.plan_state import PlanStore


def _plan(root):
    plan = root / ".meow" / "plans" / "feature.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# Feature\n", encoding="utf-8")
    return plan


def test_registry_names_every_hook_handler():
    assert set(HANDLERS) == {
        "lint_after_edit",
        "shaping_ripple",
        "capture_completed_plan",
        "validate_plan_stop",
    }


def test_non_object_event_is_rejected_by_every_handler():
    for handler in HANDLERS.values():
        assert handler(["not", "an", "object"]) == {
            "ok": False,
            "kind": "invalid-event",
            "message": "event must be a JSON object",
        }


def test_lint_after_edit_skips_edits_made_by_meow_sdk_sessions(tmp_path):
    result = lint_after_edit({
        "meow_session": "generator",
        "file_path": "x.py",
        "working_dir": str(tmp_path),
    })
    assert result == {
        "ok": True,
        "quiet": True,
        "reason": "MEOW SDK lint already handles this edit",
    }


def test_lint_after_edit_ignores_non_file_tools(tmp_path):
    result = lint_after_edit({
        "tool_name": "Bash",
        "file_path": "x.py",
        "working_dir": str(tmp_path),
    })
    assert result == {"ok": True, "quiet": True, "reason": "not a file edit"}


def test_lint_after_edit_without_project_directory_is_quiet():
    assert lint_after_edit({"file_path": "x.py"}) == {
        "ok": True,
        "quiet": True,
        "reason": "no project edit",
    }


def test_lint_after_edit_passes_when_no_per_file_lint_is_configured(tmp_path):
    (tmp_path / "x.py").write_text("x = 1\n", encoding="utf-8")
    result = lint_after_edit({"file_path": "x.py", "working_dir": str(tmp_path)})
    assert result["ok"] is True
    assert result["quiet"] is True
    assert result["failures"] == []


def test_shaping_ripple_advises_only_for_shaping_artifacts():
    advisory = shaping_ripple({"file_path": "docs/shaping/breadboard.md"})
    assert advisory["advisory"] is True
    assert advisory["ok"] is True
    assert shaping_ripple({"file_path": "src/app.py"}) == {"ok": True, "quiet": True}


def test_capture_completed_plan_records_draft_lifecycle_and_run(tmp_path):
    plan = _plan(tmp_path)
    result = capture_completed_plan({
        "working_dir": str(tmp_path),
        "plan_file": str(plan),
        "run_id": "run-1",
    })
    assert result == {
        "ok": True,
        "plan": str(plan.resolve()),
        "lifecycle": "draft",
    }
    assert PlanStore(tmp_path).read(plan).run_id == "run-1"


def test_capture_completed_plan_reports_missing_plan_file(tmp_path):
    result = capture_completed_plan({
        "working_dir": str(tmp_path),
        "plan_file": "missing.md",
    })
    assert result["ok"] is False
    assert result["kind"] == "diagnostic"
    assert "plan does not exist" in result["message"]


def test_capture_completed_plan_ignores_plans_outside_project(tmp_path):
    outside = tmp_path.parent / "outside-plan.md"
    result = capture_completed_plan({
        "working_dir": str(tmp_path),
        "plan_file": str(outside),
    })
    assert result == {
        "ok": True,
        "quiet": True,
        "reason": "no canonical plan event",
    }


def test_validate_plan_stop_advises_until_plan_is_complete(tmp_path):
    plan = _plan(tmp_path)
    event = {"working_dir": str(tmp_path), "plan_file": str(plan)}

    draft = validate_plan_stop(event)
    assert draft["advisory"] is True
    assert "Plan lifecycle is draft" in draft["message"]

    PlanStore(tmp_path).transition(plan, "complete")
    assert validate_plan_stop(event) == {
        "ok": True,
        "quiet": True,
        "lifecycle": "complete",
    }


def test_lint_after_edit_reads_claude_code_post_tool_use_events(tmp_path):
    edited = tmp_path / "x.py"
    edited.write_text("x = 1\n", encoding="utf-8")
    result = lint_after_edit({
        "hook_event_name": "PostToolUse",
        "cwd": str(tmp_path),
        "tool_name": "Write",
        "tool_input": {"file_path": str(edited), "content": "x = 1\n"},
    })
    assert result["file"] == str(edited)
    assert result["failures"] == []


def test_shaping_ripple_reads_claude_code_tool_input():
    advisory = shaping_ripple({
        "tool_name": "Edit",
        "tool_input": {"file_path": "docs/shaping/breadboard.md"},
    })
    assert advisory["advisory"] is True


def test_advisories_use_fields_claude_code_reads(tmp_path):
    advisory = shaping_ripple({"file_path": "docs/shaping/breadboard.md"})
    context = advisory["hookSpecificOutput"]
    assert context["hookEventName"] == "PostToolUse"
    assert "Shaping artifact changed" in context["additionalContext"]
    assert "hookSpecificOutput" not in shaping_ripple({"file_path": "src/app.py"})

    plan = _plan(tmp_path)
    stop = validate_plan_stop({"cwd": str(tmp_path), "plan_file": str(plan)})
    assert stop["systemMessage"] == stop["message"]
