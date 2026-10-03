import json

from meow.cli.core import _build_arg_parser, _dispatch
from meow.hooks.claude import (
    COMMANDS,
    MANIFEST,
    SETTINGS,
    inspect_claude_hooks,
    install_claude_hooks,
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
