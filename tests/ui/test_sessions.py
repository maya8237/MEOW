from __future__ import annotations

import subprocess
from pathlib import Path
from uuid import UUID

import pytest

from meow.ui import sessions
from meow.ui.sessions import (
    RecipeError,
    command_for,
    detect_capabilities,
    new_id,
    recipe_json,
    validate_recipe,
)


def valid_payload(**overrides):
    payload = {
        "name": "API update",
        "mode": "cli",
        "command": "run",
        "request": "Add an API endpoint",
        "workflow": None,
        "inputs": {},
        "arguments": [],
        "options": {},
    }
    payload.update(overrides)
    return payload


def test_feature_recipe_builds_unattended_isolated_cli_argv(tmp_path):
    recipe = validate_recipe(
        tmp_path,
        valid_payload(
            repo=str(tmp_path),
            workflow="meow-custom:quick",
            inputs={"scope": "users"},
        ),
    )

    assert command_for(recipe, python="python-test") == [
        "python-test",
        "-u",
        "-m",
        "meow",
        "run",
        "--name",
        "API update",
        "--workflow",
        "meow-custom:quick",
        "--input",
        "scope=users",
        "--unattended",
        "Add an API endpoint",
    ]


@pytest.mark.parametrize("command", ["ui", "ipython", "_worker", "detach"])
def test_recipe_rejects_interactive_or_internal_commands(tmp_path, command):
    with pytest.raises(RecipeError):
        validate_recipe(tmp_path, valid_payload(command=command))


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": "-unsafe"},
        {"inputs": {"scope": "bad\x00value"}},
        {"workflow": "bad\x00workflow"},
    ],
)
def test_recipe_rejects_values_that_cannot_be_passed_as_argv(tmp_path, overrides):
    with pytest.raises(RecipeError):
        validate_recipe(tmp_path, valid_payload(**overrides))


def test_recipe_ids_are_server_generated_uuid_hex(tmp_path):
    recipe = validate_recipe(tmp_path, valid_payload(id="attacker-chosen"))

    assert UUID(hex=recipe.id).hex == recipe.id
    assert new_id() != recipe.id
    assert recipe_json(recipe)["id"] == recipe.id


def test_cli_recipe_rejects_server_owned_and_interactive_arguments(tmp_path):
    for argument in ("--work-dir", "--background", "--manually-approve-plan"):
        with pytest.raises(RecipeError):
            validate_recipe(
                tmp_path,
                valid_payload(arguments=[argument, "elsewhere"]),
            )


def test_cli_recipe_uses_parser_to_reject_unknown_and_invalid_options(tmp_path):
    with pytest.raises(RecipeError, match="Unknown run option"):
        validate_recipe(tmp_path, valid_payload(arguments=["--made-up"]))

    in_place = validate_recipe(
        tmp_path,
        valid_payload(options={"no_worktree": True}),
    )
    argv = command_for(in_place)
    assert "--no-worktree" in argv
    assert "--unattended" not in argv


def test_cli_recipe_rejects_duplicate_singleton_flags(tmp_path):
    with pytest.raises(RecipeError, match="Duplicate singleton"):
        validate_recipe(
            tmp_path,
            valid_payload(arguments=["--test", "--test"]),
        )


def test_plan_recipe_does_not_receive_unattended_flag(tmp_path):
    recipe = validate_recipe(
        tmp_path,
        valid_payload(command="plan", name="Plan API", repo=str(tmp_path)),
    )

    assert "--unattended" not in command_for(recipe)


def test_review_recipe_allows_only_headless_review_form(tmp_path):
    recipe = validate_recipe(
        tmp_path,
        valid_payload(command="review", arguments=["--fix"], request="Review API"),
    )
    assert command_for(recipe)[4:] == ["review", "--fix", "Review API"]

    with pytest.raises(RecipeError):
        validate_recipe(
            tmp_path,
            valid_payload(command="review", arguments=["--ci", "--fix"]),
        )


def test_claude_recipe_defaults_to_prompting_denied_policy(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "claude_path", lambda: "claude-test")
    monkeypatch.setattr(
        sessions,
        "detect_capabilities",
        lambda repo: {
            "available": True,
            "version": "1.2.3",
            "missing": [],
            "permission_policy": "dontAsk+none",
            "bypass_supported": True,
            "error": None,
        },
    )
    recipe = validate_recipe(tmp_path, valid_payload(mode="claude"))

    assert recipe.options["permission_policy"] == "default"
    argv = command_for(recipe)
    assert argv[0] == "claude-test"
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--permission-prompts") + 1] == "none"
    assert "--allow-dangerously-skip-permissions" not in argv
    assert "--permission-prompt-tool" not in argv


def test_claude_bypass_requires_both_explicit_flags(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "claude_path", lambda: "claude-test")
    monkeypatch.setattr(
        sessions,
        "detect_capabilities",
        lambda repo: {
            "available": True,
            "version": "1.2.3",
            "missing": [],
            "permission_policy": "dontAsk+none",
            "bypass_supported": True,
            "error": None,
        },
    )
    recipe = validate_recipe(
        tmp_path,
        valid_payload(mode="claude", options={"permission_policy": "bypass"}),
    )

    argv = command_for(recipe)
    assert "--allow-dangerously-skip-permissions" in argv
    assert "bypassPermissions" in argv


def test_missing_claude_partial_message_flag_is_reported(monkeypatch):
    monkeypatch.setattr(sessions, "claude_path", lambda: "claude-test")
    monkeypatch.setattr(sessions, "claude_help", lambda: "--print --output-format")

    with pytest.raises(RecipeError, match="partial messages"):
        detect_capabilities(Path("/repo"))


def test_capability_probe_checks_help_and_version_without_starting_session(
    tmp_path, monkeypatch
):
    help_text = """Usage: claude [options]
      --print
      --output-format <format> (text, json, stream-json)
      --verbose
      --include-partial-messages
      --resume <session>
      --permission-mode <mode> (dontAsk, bypassPermissions)
      --permission-prompts <mode> (none)
      --allow-dangerously-skip-permissions
"""
    calls = []
    monkeypatch.setattr(sessions, "claude_path", lambda: "claude-test")

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout="1.2.3\n" if argv[-1] == "--version" else help_text,
            stderr="",
        )

    monkeypatch.setattr(sessions.subprocess, "run", fake_run)
    capabilities = detect_capabilities(tmp_path)

    assert capabilities == {
        "available": True,
        "version": "1.2.3",
        "missing": [],
        "permission_policy": "dontAsk+none",
        "bypass_supported": True,
        "error": None,
    }
    assert [call[0] for call in calls] == [
        ["claude-test", "--help"],
        ["claude-test", "--version"],
    ]
    assert all(call[1]["shell"] is False for call in calls)


def test_claude_capability_probe_reports_unavailable_and_missing_executable(
    monkeypatch,
):
    monkeypatch.setattr(sessions, "claude_path", lambda: None)

    capabilities = detect_capabilities(Path("/repo"))

    assert capabilities["available"] is False
    assert capabilities["error"]
    assert "claude" in capabilities["error"].lower()


def test_cli_display_is_informational_and_round_trips_special_characters(tmp_path):
    recipe = validate_recipe(
        tmp_path,
        valid_payload(request="Add spaces, quotes ' and unicode 🐈"),
    )

    argv = command_for(recipe)

    assert argv[-1] == "Add spaces, quotes ' and unicode 🐈"
    assert "Add spaces" not in " ".join(argv[:-1])
