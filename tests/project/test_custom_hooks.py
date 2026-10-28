"""TOML hook files: validation, scopes, and role filtering."""

import pytest

from meow.project.config import load_config

HOOK = """\
description = "Guard edits"
event = "PreToolUse"
matcher = "Write|Edit"
command = "true"
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".meow").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    root = tmp_path / "project"
    (root / ".meow").mkdir(parents=True)
    return root


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def hooks(project):
    return load_config(project)["customizations"].hooks


def test_conventional_hook_is_discovered(project):
    write(project / ".meow" / "hooks" / "guard.toml", HOOK)

    (hook,) = hooks(project)

    assert (hook.name, hook.event, hook.matcher, hook.timeout) == (
        "guard",
        "PreToolUse",
        "Write|Edit",
        30.0,
    )
    assert hook.applies_to("generator") and hook.applies_to("custom_role")


def test_local_scope_replaces_project_hook(project):
    write(project / ".meow" / "hooks" / "guard.toml", HOOK)
    write(
        project / ".meow" / "local" / "hooks" / "guard.toml",
        HOOK.replace("true", "false"),
    )

    (hook,) = hooks(project)

    assert hook.command == "false" and hook.source.scope == "local"


def test_roles_limit_where_a_hook_applies(project):
    write(project / ".meow" / "hooks" / "guard.toml", HOOK + 'roles = ["generator"]\n')

    custom = load_config(project)["customizations"]

    assert [h.name for h in custom.hooks_for("PreToolUse", "generator")] == ["guard"]
    assert custom.hooks_for("PreToolUse", "reviewer") == []
    assert custom.hooks_for("PostToolUse", "generator") == []


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (HOOK.replace("PreToolUse", "Nope"), "'event' must be one of"),
        (HOOK.replace('command = "true"\n', ""), "must set 'command'"),
        (HOOK + "extra = 1\n", "unknown key"),
        (HOOK + "timeout = 0\n", "positive number"),
        (HOOK.replace("Write|Edit", "("), "valid regex"),
        (
            'description = "x"\nevent = "Stop"\ncommand = "true"\nmatcher = "x"\n',
            "applies only to",
        ),
        (
            'description = "x"\nevent = "sprint_end"\ncommand = "true"\n'
            'roles = ["a"]\n',
            "drop 'roles'",
        ),
    ],
)
def test_invalid_hook_is_rejected(project, text, message):
    write(project / ".meow" / "hooks" / "guard.toml", text)

    with pytest.raises(ValueError, match=message):
        hooks(project)
