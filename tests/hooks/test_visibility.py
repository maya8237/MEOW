import json

from meow.cli.cli import _build_arg_parser, _dispatch
from meow.hooks.claude import (
    COMMANDS,
    MANIFEST,
    SETTINGS,
    inspect_claude_hooks,
    install_claude_hooks,
    uninstall_claude_hooks,
)


def test_inspect_hooks_reports_absent_without_writes(tmp_path):
    assert inspect_claude_hooks(tmp_path) == {name: "missing" for name in COMMANDS}
    assert not (tmp_path / SETTINGS).exists()


def test_inspect_hooks_detects_active_missing_and_modified(tmp_path):
    install_claude_hooks(tmp_path, ["lint", "shaping"])
    assert inspect_claude_hooks(tmp_path)["lint"] == "active"
    settings_path = tmp_path / SETTINGS
    settings = json.loads(settings_path.read_text())
    settings["hooks"]["PostToolUse"][0]["matcher"] = "Write"
    settings_path.write_text(json.dumps(settings))
    statuses = inspect_claude_hooks(tmp_path)
    assert statuses["lint"] == "modified"
    assert statuses["shaping"] == "active"
    assert statuses["plan_capture"] == "missing"


def test_inspect_hooks_reports_malformed_manifest_without_writes(tmp_path):
    path = tmp_path / MANIFEST
    path.parent.mkdir(parents=True)
    path.write_text("{broken")
    before = path.read_bytes()
    assert inspect_claude_hooks(tmp_path)["_diagnostic"] == "malformed manifest"
    assert path.read_bytes() == before


def test_cli_reports_hook_status(tmp_path, capsys):
    args = _build_arg_parser().parse_args(["hooks", "status", "claude"])
    _dispatch(args, tmp_path, use_worktree=False)
    assert json.loads(capsys.readouterr().out)["lint"] == "missing"


def test_repeated_install_preserves_unrelated_user_hook(tmp_path):
    settings_path = tmp_path / SETTINGS
    settings_path.parent.mkdir(parents=True)
    user_entry = {
        "matcher": "Read",
        "hooks": [{"type": "command", "command": "my-hook"}],
    }
    settings_path.write_text(json.dumps({"hooks": {"PostToolUse": [user_entry]}}))
    install_claude_hooks(tmp_path, ["lint"])
    install_claude_hooks(tmp_path, ["lint"])
    entries = json.loads(settings_path.read_text())["hooks"]["PostToolUse"]
    assert entries.count(user_entry) == 1
    assert sum(COMMANDS["lint"] in str(entry) for entry in entries) == 1
    assert inspect_claude_hooks(tmp_path)["lint"] == "active"


def test_removed_hook_is_missing_when_another_hook_shares_event(tmp_path):
    install_claude_hooks(tmp_path, ["lint", "shaping"])
    settings_path = tmp_path / SETTINGS
    settings = json.loads(settings_path.read_text())
    settings["hooks"]["PostToolUse"].pop(0)
    settings_path.write_text(json.dumps(settings))
    statuses = inspect_claude_hooks(tmp_path)
    assert statuses["lint"] == "missing"
    assert statuses["shaping"] == "active"


def test_separate_install_calls_keep_manifest_ownership(tmp_path):
    install_claude_hooks(tmp_path, ["lint"])
    install_claude_hooks(tmp_path, ["shaping"])
    statuses = inspect_claude_hooks(tmp_path)
    assert statuses["lint"] == "active"
    assert statuses["shaping"] == "active"


def _old_stop_install(root):
    old = {
        "matcher": "",
        "hooks": [{"type": "command", "command": COMMANDS["plan_capture"]}],
    }
    (root / ".claude").mkdir(parents=True, exist_ok=True)
    (root / ".meow").mkdir(parents=True, exist_ok=True)
    (root / SETTINGS).write_text(json.dumps({"hooks": {"Stop": [old]}}))
    (root / MANIFEST).write_text(
        json.dumps({"version": 1, "entries": [{"event": "Stop", "entry": old}]})
    )


def test_plan_capture_installs_on_post_tool_use_and_plan_stop_on_stop(tmp_path):
    install_claude_hooks(tmp_path, ["plan_capture", "plan_stop"])
    hooks = json.loads((tmp_path / SETTINGS).read_text())["hooks"]
    assert [e["matcher"] for e in hooks["PostToolUse"]] == ["Write|Edit"]
    assert hooks["PostToolUse"][0]["hooks"][0]["command"] == COMMANDS["plan_capture"]
    assert len(hooks["Stop"]) == 1
    assert hooks["Stop"][0]["hooks"][0]["command"] == COMMANDS["plan_stop"]


def test_install_migrates_old_stop_based_plan_capture(tmp_path):
    _old_stop_install(tmp_path)
    install_claude_hooks(tmp_path, ["plan_capture"])
    hooks = json.loads((tmp_path / SETTINGS).read_text())["hooks"]
    assert hooks["Stop"] == []
    assert len(hooks["PostToolUse"]) == 1
    manifest = json.loads((tmp_path / MANIFEST).read_text())
    assert [i["event"] for i in manifest["entries"]] == ["PostToolUse"]
    before = ((tmp_path / SETTINGS).read_text(), (tmp_path / MANIFEST).read_text())
    install_claude_hooks(tmp_path, ["plan_capture"])
    after = ((tmp_path / SETTINGS).read_text(), (tmp_path / MANIFEST).read_text())
    assert before == after


def test_install_skips_manifest_item_without_event(tmp_path):
    _old_stop_install(tmp_path)
    manifest_path = tmp_path / MANIFEST
    manifest = json.loads(manifest_path.read_text())
    del manifest["entries"][0]["event"]
    manifest_path.write_text(json.dumps(manifest))

    result = install_claude_hooks(tmp_path, list(COMMANDS))

    assert result["installed"] == list(COMMANDS)
    entries = json.loads((tmp_path / SETTINGS).read_text())["hooks"]["PostToolUse"]
    assert (
        sum(
            entry["hooks"][0]["command"] == COMMANDS["plan_capture"]
            for entry in entries
        )
        == 1
    )


def test_install_skips_manifest_item_with_non_string_event(tmp_path):
    _old_stop_install(tmp_path)
    manifest_path = tmp_path / MANIFEST
    manifest = json.loads(manifest_path.read_text())
    manifest["entries"][0]["event"] = ["Stop"]
    manifest_path.write_text(json.dumps(manifest))

    result = install_claude_hooks(tmp_path, list(COMMANDS))

    assert result["installed"] == list(COMMANDS)
    entries = json.loads((tmp_path / SETTINGS).read_text())["hooks"]["PostToolUse"]
    assert (
        sum(
            entry["hooks"][0]["command"] == COMMANDS["plan_capture"]
            for entry in entries
        )
        == 1
    )


def test_migration_preserves_foreign_and_lookalike_stop_entries(tmp_path):
    _old_stop_install(tmp_path)
    settings_path = tmp_path / SETTINGS
    settings = json.loads(settings_path.read_text())
    foreign = {"matcher": "", "hooks": [{"type": "command", "command": "mine"}]}
    lookalike = {
        "matcher": "",
        "hooks": [
            {"type": "command", "command": COMMANDS["plan_capture"]},
            {"type": "command", "command": "mine-too"},
        ],
    }
    settings["hooks"]["Stop"].extend([foreign, lookalike])
    settings_path.write_text(json.dumps(settings))

    install_claude_hooks(tmp_path, ["plan_capture"])

    hooks = json.loads(settings_path.read_text())["hooks"]
    assert hooks["Stop"] == [foreign, lookalike]


def test_inspect_reports_wrong_event_as_modified(tmp_path):
    _old_stop_install(tmp_path)
    assert inspect_claude_hooks(tmp_path)["plan_capture"] == "modified"


def test_uninstall_removes_old_and_new_entries(tmp_path):
    _old_stop_install(tmp_path)
    install_claude_hooks(tmp_path, ["plan_capture", "plan_stop"])
    uninstall_claude_hooks(tmp_path)
    hooks = json.loads((tmp_path / SETTINGS).read_text())["hooks"]
    assert all(not v for v in hooks.values())
    assert not (tmp_path / MANIFEST).exists()
