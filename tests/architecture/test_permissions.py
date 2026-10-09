"""Project permission rules must be unambiguous and fail closed."""

import asyncio

import pytest

from meow.agents.base import Agent, ProjectContext
from meow.project.config import load_config
from meow.project.permissions import make_permission_callback, parse_policy


def test_role_and_path_rules_do_not_bleed_into_other_roles():
    policy = parse_policy({
        "rule": [
            {"role": "generator", "tool": "Write", "action": "deny", "path": ".env"},
        ]
    })
    assert (
        policy.for_role("generator").decision("Write", {"file_path": ".env"}) == "deny"
    )
    assert policy.for_role("reviewer").decision("Write", {"file_path": ".env"}) is None
    assert (
        policy.for_role("generator").decision("Write", {"file_path": "src/app.py"})
        is None
    )


@pytest.mark.parametrize(
    "raw",
    [
        {
            "rule": [
                {
                    "role": "generator",
                    "tool": "Write",
                    "action": "allow",
                    "path": "../secret",
                }
            ]
        },
        {
            "rule": [
                {
                    "role": "generator",
                    "tool": "Bash",
                    "action": "allow",
                    "command": "git status",
                }
            ]
        },
        {
            "rule": [
                {"role": "generator", "tool": "Write", "action": "ask"},
                {"role": "generator", "tool": "Write", "action": "deny"},
            ]
        },
        {"rule": [{"role": "generator", "tool": "Write", "action": "oops"}]},
        {
            "rule": [
                {"role": "generator", "tool": "Bash", "action": "deny", "path": ".env"}
            ]
        },
        {"rule": [{"role": "explorer", "tool": "Read", "action": "deny"}]},
        {"rule": [{"role": "unknown", "tool": "Write", "action": "deny"}]},
    ],
)
def test_invalid_or_unenforceable_rules_are_rejected(raw):
    with pytest.raises(ValueError):
        parse_policy(raw)


def test_config_loads_permission_rules(tmp_path):
    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow" / "config.toml").write_text(
        'lint_command = "ruff check"\n[permissions]\n'
        '[[permissions.rule]]\nrole = "generator"\n'
        'tool = "Write"\naction = "deny"\npath = ".env"\n',
        encoding="utf-8",
    )
    policy = load_config(tmp_path)["permissions"]
    assert (
        policy.for_role("generator").decision("Write", {"file_path": ".env"}) == "deny"
    )


def test_callback_denies_absolute_path_inside_project(tmp_path):
    policy = parse_policy({
        "rule": [
            {"role": "generator", "tool": "Write", "action": "deny", "path": ".env"},
        ]
    })
    callback = make_permission_callback(
        policy.for_role("generator"), unattended=True, project_root=tmp_path
    )
    result = asyncio.run(callback("Write", {"file_path": str(tmp_path / ".env")}, None))
    assert result.behavior == "deny"
    other = asyncio.run(
        callback("Write", {"file_path": str(tmp_path / "src/app.py")}, None)
    )
    assert other.behavior == "allow"


def test_unattended_ask_interrupts_without_prompt():
    policy = parse_policy({
        "rule": [
            {"role": "generator", "tool": "Bash", "action": "ask"},
        ]
    })
    callback = make_permission_callback(policy.for_role("generator"), unattended=True)
    result = asyncio.run(callback("Bash", {"command": "git push"}, None))
    assert result.behavior == "deny"
    assert result.interrupt is True


def test_path_rules_close_bash_and_agent_escape_routes():
    policy = parse_policy({"rule": [
        {"role": "generator", "tool": "Write", "action": "deny", "path": ".env"},
    ]}).for_role("generator")
    assert policy.decision("Bash", {"command": "type .env"}) == "deny"
    assert policy.decision("Agent", {"prompt": "read .env"}) == "deny"


@pytest.mark.parametrize("tool", ["Bash", "Agent"])
@pytest.mark.parametrize("action", ["allow", "ask"])
def test_path_rules_reject_open_escape_routes(tool, action):
    with pytest.raises(ValueError, match="path-scoped"):
        parse_policy({"rule": [
            {"role": "generator", "tool": "Write", "action": "deny", "path": ".env"},
            {"role": "generator", "tool": tool, "action": action},
        ]})


def test_agent_options_attach_policy_only_when_configured(tmp_path):
    config = {"models": {"generator": None}, "lint": []}
    agent = Agent(ProjectContext(tmp_path, config))
    assert (
        agent.options(
            system_prompt="x", allowed_tools=["Write"], role="generator"
        ).can_use_tool
        is None
    )
    config["permissions"] = parse_policy({
        "rule": [
            {"role": "generator", "tool": "Write", "action": "deny"},
        ]
    })
    callback = agent.options(
        system_prompt="x", allowed_tools=["Write"], role="generator"
    ).can_use_tool
    assert callback is not None
    assert asyncio.run(callback("Write", {"file_path": "x"}, None)).behavior == "deny"
