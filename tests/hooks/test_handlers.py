from meow.hooks.handlers import (
    HANDLERS,
    lint_after_edit,
)


def test_registry_names_every_hook_handler():
    assert set(HANDLERS) == {
        "lint_after_edit",
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
