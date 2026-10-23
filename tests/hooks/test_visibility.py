import json

import pytest

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
    assert inspect_claude_hooks(tmp_path) == {"lint": "missing"}
    assert not (tmp_path / SETTINGS).exists()


def test_inspect_hooks_detects_active_and_modified(tmp_path):
    install_claude_hooks(tmp_path, ["lint"])
    assert inspect_claude_hooks(tmp_path)["lint"] == "active"
    settings_path = tmp_path / SETTINGS
    settings = json.loads(settings_path.read_text())
    settings["hooks"]["PostToolUse"][0]["matcher"] = "Write"
    settings_path.write_text(json.dumps(settings))
    assert inspect_claude_hooks(tmp_path)["lint"] == "modified"


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


def test_removed_hook_is_missing_when_user_hook_shares_event(tmp_path):
    install_claude_hooks(tmp_path, ["lint"])
    settings_path = tmp_path / SETTINGS
    settings = json.loads(settings_path.read_text())
    settings["hooks"]["PostToolUse"] = [
        {"matcher": "Write|Edit", "hooks": [{"type": "command", "command": "mine"}]}
    ]
    settings_path.write_text(json.dumps(settings))
    assert inspect_claude_hooks(tmp_path)["lint"] == "missing"


def _wrong_event_install(root):
    old = {
        "matcher": "",
        "hooks": [{"type": "command", "command": COMMANDS["lint"]}],
    }
    (root / ".claude").mkdir(parents=True, exist_ok=True)
    (root / ".meow").mkdir(parents=True, exist_ok=True)
    (root / SETTINGS).write_text(json.dumps({"hooks": {"Stop": [old]}}))
    (root / MANIFEST).write_text(
        json.dumps({"version": 1, "entries": [{"event": "Stop", "entry": old}]})
    )


def test_reinstall_repairs_event_binding(tmp_path):
    _wrong_event_install(tmp_path)
    install_claude_hooks(tmp_path, ["lint"])
    hooks = json.loads((tmp_path / SETTINGS).read_text())["hooks"]
    assert hooks["Stop"] == []
    assert len(hooks["PostToolUse"]) == 1
    manifest = json.loads((tmp_path / MANIFEST).read_text())
    assert [i["event"] for i in manifest["entries"]] == ["PostToolUse"]


@pytest.mark.parametrize("event", [None, ["Stop"]])
def test_install_skips_manifest_item_without_string_event(tmp_path, event):
    _wrong_event_install(tmp_path)
    manifest_path = tmp_path / MANIFEST
    manifest = json.loads(manifest_path.read_text())
    manifest["entries"][0]["event"] = event
    manifest_path.write_text(json.dumps(manifest))

    result = install_claude_hooks(tmp_path, ["lint"])

    assert result["installed"] == ["lint"]
    entries = json.loads((tmp_path / SETTINGS).read_text())["hooks"]["PostToolUse"]
    assert len(entries) == 1
    assert entries[0]["hooks"][0]["command"] == COMMANDS["lint"]


def test_inspect_reports_wrong_event_as_modified(tmp_path):
    _wrong_event_install(tmp_path)
    assert inspect_claude_hooks(tmp_path)["lint"] == "modified"


def test_uninstall_removes_owned_entries(tmp_path):
    _wrong_event_install(tmp_path)
    uninstall_claude_hooks(tmp_path)
    hooks = json.loads((tmp_path / SETTINGS).read_text())["hooks"]
    assert all(not v for v in hooks.values())
    assert not (tmp_path / MANIFEST).exists()
