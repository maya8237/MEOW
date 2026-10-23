"""Retired Claude hooks cannot be installed and are cleaned up on reinstall."""

import json

import pytest

from meow.cli.cli import _build_arg_parser, _dispatch
from meow.hooks.claude import MANIFEST, SETTINGS, install_claude_hooks


def test_default_install_only_adds_lint(tmp_path, capsys):
    args = _build_arg_parser().parse_args(["hooks", "install", "claude"])
    _dispatch(args, tmp_path, use_worktree=False)
    assert json.loads(capsys.readouterr().out)["installed"] == ["lint"]
    hooks = json.loads((tmp_path / SETTINGS).read_text())["hooks"]
    assert list(hooks) == ["PostToolUse"]
    assert len(hooks["PostToolUse"]) == 1


@pytest.mark.parametrize(
    ("name", "handler"),
    [
        ("shaping", "shaping_ripple"),
        ("plan_capture", "capture_completed_plan"),
        ("plan_stop", "validate_plan_stop"),
    ],
)
def test_retired_hooks_cannot_be_invoked_or_installed(name, handler):
    with pytest.raises(SystemExit):
        _build_arg_parser().parse_args(["hooks", "install", "claude", "--only", name])
    with pytest.raises(SystemExit):
        _build_arg_parser().parse_args(["native", "hook", handler])


@pytest.mark.parametrize(
    ("event", "handler"),
    [
        ("PostToolUse", "shaping_ripple"),
        ("PostToolUse", "capture_completed_plan"),
        ("Stop", "capture_completed_plan"),
        ("Stop", "validate_plan_stop"),
    ],
)
def test_reinstall_removes_retired_owned_entries_only(tmp_path, event, handler):
    old = {
        "matcher": "Write|Edit" if event == "PostToolUse" else "",
        "hooks": [{"type": "command", "command": f"meow native hook {handler}"}],
    }
    lookalike = {
        **old,
        "hooks": [*old["hooks"], {"type": "command", "command": "echo keep"}],
    }
    settings_path, manifest_path = tmp_path / SETTINGS, tmp_path / MANIFEST
    settings_path.parent.mkdir()
    manifest_path.parent.mkdir()
    settings_path.write_text(
        json.dumps({
            "permissions": {"allow": ["Read"]},
            "hooks": {event: [old, lookalike]},
        })
    )
    manifest_path.write_text(
        json.dumps({"version": 1, "entries": [{"event": event, "entry": old}]})
    )
    before = (settings_path.read_bytes(), manifest_path.read_bytes())

    install_claude_hooks(tmp_path, ["lint"], dry_run=True)
    assert (settings_path.read_bytes(), manifest_path.read_bytes()) == before

    install_claude_hooks(tmp_path, ["lint"])
    settings = json.loads(settings_path.read_text())
    assert old not in settings["hooks"][event]
    assert lookalike in settings["hooks"][event]
    assert settings["permissions"] == {"allow": ["Read"]}
    manifest = json.loads(manifest_path.read_text())
    assert [item["entry"]["hooks"][0]["command"] for item in manifest["entries"]] == [
        "meow native hook lint_after_edit"
    ]
    before = (settings_path.read_bytes(), manifest_path.read_bytes())
    install_claude_hooks(tmp_path, ["lint"])
    assert (settings_path.read_bytes(), manifest_path.read_bytes()) == before
