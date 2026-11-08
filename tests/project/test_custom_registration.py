"""Keeping every customization kind working at every scope, automatically."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from meow.project.config import load_config
from meow.project.custom.registration import (
    auto_sync_message,
    check_registration,
    hook_output,
)
from meow.project.custom.sources import unshared_project_dirs
from meow.project.onboarding import BOUNDARY

ROOT = Path(__file__).resolve().parents[2]
HOOK = 'description = "Guard"\nevent = "PreToolUse"\ncommand = "true"\n'


def _shell_path(path: Path) -> str:
    value = str(path).replace("\\", "/")
    drive, tail = os.path.splitdrive(value)
    if os.name == "nt" and drive:
        value = f"/{drive[0].lower()}{tail}"
    return value


@pytest.fixture
def home(tmp_path, monkeypatch):
    user_home = tmp_path / "home"
    (user_home / ".meow").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.setenv("USERPROFILE", str(user_home))
    return user_home


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    (root / ".meow").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / ".gitignore").write_text("\n".join(BOUNDARY) + "\n", encoding="utf-8")
    return root


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def skill(folder: Path, name: str) -> None:
    write(
        folder / name / "SKILL.md",
        f"---\nname: {name}\ndescription: Does {name}.\n---\n\nBody.\n",
    )


def plugins(project: Path, home: Path) -> dict[str, Path]:
    """Each scope's plugin root."""
    skills = project / ".claude" / "skills"
    return {
        "local": skills / "meow-local",
        "project": skills / "meow-project",
        "user": home / ".meow" / "plugin",
    }


def every_scope(project: Path, home: Path) -> None:
    """A skill and a hook at local, project, and user scope, plus a linked skill."""
    bases = {
        "local": project / ".meow" / "local",
        "project": project / ".meow",
        "user": home / ".meow",
    }
    for scope, base in bases.items():
        skill(plugins(project, home)[scope] / "skills", f"s-{scope}")
        write(base / "hooks" / f"guard-{scope}.toml", HOOK.replace("Guard", scope))
    skill(project / "tools" / "skills", "linked-one")
    write(project / ".meow" / "config.toml", '[custom]\nskills = ["tools/skills"]\n')


def custom(project: Path):
    return load_config(project)["customizations"]


def settings(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_auto_sync_fixes_every_scope(project, home):
    every_scope(project, home)

    message = auto_sync_message(custom(project), project, home / ".meow")

    assert message and "Claude Code" in message
    assert "need attention" not in message
    # Skills and hooks land where Claude Code watches them: no restart.
    for scope, base in {
        "local": project / ".claude" / "skills",
        "project": project / ".claude" / "skills",
        "user": home / ".claude" / "skills",
    }.items():
        assert (base / f"s-{scope}" / "SKILL.md").is_file()
    assert (project / ".claude/skills/linked-one/SKILL.md").is_file()
    for path in (
        project / ".claude" / "settings.json",
        project / ".claude" / "settings.local.json",
        home / ".claude" / "settings.json",
    ):
        assert "MEOW_HOOK=guard" in path.read_text()
    assert check_registration(custom(project), project, home / ".meow") == []
    assert auto_sync_message(custom(project), project, home / ".meow") is None


def test_auto_sync_picks_up_later_edits(project, home):
    every_scope(project, home)
    auto_sync_message(custom(project), project, home / ".meow")

    write(
        project / ".meow" / "hooks" / "guard-project.toml",
        HOOK + 'matcher = "Bash"\n',
    )
    skill(project / "tools" / "skills", "linked-two")

    message = auto_sync_message(custom(project), project, home / ".meow")

    assert message and "out of date" in message
    assert '"matcher": "^(?:Bash)$"' in (project / ".claude/settings.json").read_text()
    assert (project / ".claude/skills/linked-two/SKILL.md").is_file()


def test_auto_sync_restores_the_gitignore_boundary(project, home):
    write(project / ".meow" / "hooks" / "guard.toml", HOOK)
    write(project / ".meow" / "config.toml", "")
    old = [rule for rule in BOUNDARY if rule != "!.meow/hooks/"]
    (project / ".gitignore").write_text("\n".join(old) + "\n", encoding="utf-8")
    assert unshared_project_dirs(project)

    message = auto_sync_message(custom(project), project, home / ".meow")

    assert message and ".gitignore: restored" in message
    assert "need attention" not in message
    assert unshared_project_dirs(project) == []


def test_one_broken_scope_does_not_stop_the_others(project, home):
    skill(plugins(project, home)["project"] / "skills", "team")
    skill(home / ".meow" / "plugin" / "skills", "mine")
    write(home / ".claude" / "settings.json", "{not json")

    message = auto_sync_message(custom(project), project, home / ".meow")

    assert message and "not valid JSON" in message
    assert (project / ".claude/skills/team/SKILL.md").is_file()


def test_ignored_project_definitions_are_reported(project, home):
    write(project / ".meow" / "hooks" / "guard.toml", HOOK)
    write(project / ".meow" / "agents" / "helper.toml", "")
    (project / ".gitignore").write_text(".meow/hooks/\n", encoding="utf-8")

    problems = unshared_project_dirs(project)

    assert len(problems) == 1
    assert "project hooks" in problems[0] and "meow onboard" in problems[0]


def test_auto_sync_reports_what_it_cannot_fix(project, home):
    write(project / ".meow" / "hooks" / "guard.toml", HOOK)
    (project / ".gitignore").write_text(".meow/\n", encoding="utf-8")

    message = auto_sync_message(custom(project), project, home / ".meow")

    assert message and "need attention" in message
    assert "project hooks" in message and "ignored by git" in message


def test_sync_auto_command_prints_hook_json_and_never_fails(project, home, capsys):
    from meow.cli.parser import _build_arg_parser
    from meow.cli.sync_cli import sync_command

    args = _build_arg_parser().parse_args(["sync", "--auto"])
    skill(plugins(project, home)["project"] / "skills", "team")
    sync_command(args, project)
    output = json.loads(capsys.readouterr().out)
    assert "/reload-plugins" in output["systemMessage"]
    assert output["hookSpecificOutput"] == {
        "hookEventName": "SessionStart",
        "reloadSkills": True,
    }

    sync_command(args, project)
    assert capsys.readouterr().out == ""

    write(project / ".meow" / "config.toml", "not = [valid")
    sync_command(args, project)
    assert "could not sync" in json.loads(capsys.readouterr().out)["systemMessage"]


def test_hook_output_is_empty_without_a_message():
    assert hook_output(None) == ""


def test_post_tool_use_output_has_no_session_start_fields():
    assert json.loads(hook_output("x", "PostToolUse")) == {"systemMessage": "x"}


def test_plugin_runs_auto_sync_when_claude_code_starts():
    config = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    (entry,) = config["hooks"]["SessionStart"]
    command = entry["hooks"][0]["command"]
    assert entry["matcher"] == "startup"
    assert "meow sync --auto" in command
    assert command.startswith("command -v meow") and command.endswith("|| true")


def plugin_edit_hook(stdin: str, tmp_path: Path) -> str:
    """Run the plugin's PostToolUse command with a fake `meow` that logs its args."""
    config = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    (entry,) = config["hooks"]["PostToolUse"]
    assert entry["matcher"] == "^(?:Write|Edit|MultiEdit)$"
    bin_dir = tmp_path / "bin"
    log = tmp_path / "calls"
    write(bin_dir / "meow", f'#!/bin/sh\necho "$@" >> {_shell_path(log)}\n')
    (bin_dir / "meow").chmod(0o755)
    result = subprocess.run(
        ["sh", "-c", entry["hooks"][0]["command"]],
        input=stdin,
        text=True,
        env={
            "PATH": f"{_shell_path(bin_dir)}:/usr/bin:/bin",
            "CLAUDE_PROJECT_DIR": str(tmp_path),
        },
        check=False,
    )
    assert result.returncode == 0
    return log.read_text() if log.exists() else ""


def test_plugin_syncs_right_after_a_customization_is_edited(tmp_path):
    edit = '{"tool_input": {"file_path": "/repo/.meow/hooks/guard.toml"}}'
    assert "sync --auto PostToolUse" in plugin_edit_hook(edit, tmp_path)


def test_plugin_ignores_unrelated_edits(tmp_path):
    edit = '{"tool_input": {"file_path": "/repo/src/app.py"}}'
    assert plugin_edit_hook(edit, tmp_path) == ""


def test_auto_sync_serves_hooks_from_configured_directories(project, home):
    write(project / "tools" / "hooks" / "cfg.toml", HOOK + 'matcher = "Bash"\n')
    write(project / ".meow" / "config.toml", '[custom]\nhooks = ["tools/hooks"]\n')

    auto_sync_message(custom(project), project, home / ".meow")

    hooks = json.loads((project / ".claude" / "settings.json").read_text())
    (entry,) = hooks["hooks"]["PreToolUse"]
    assert entry["matcher"] == "^(?:Bash)$"
    assert "MEOW_HOOK=cfg" in entry["hooks"][0]["command"]
    assert check_registration(custom(project), project, home / ".meow") == []


GENERATED = ".claude/skills/meow-project-linked/skills/x/SKILL.md"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    )


def test_protect_generated_restores_rules_and_untracks(project):
    from meow.project.onboarding import protect_generated

    write(project / GENERATED, "x")
    (project / ".gitignore").write_text("", encoding="utf-8")
    _git(project, "add", "-f", GENERATED)
    changed = protect_generated(project)
    assert any("restored" in line for line in changed)
    assert any("stopped tracking" in line for line in changed)
    assert _git(project, "ls-files", GENERATED).stdout == ""
    assert (project / GENERATED).is_file()
    assert _git(project, "check-ignore", "-q", GENERATED).returncode == 0
    assert protect_generated(project) == []


def test_protect_generated_ignores_unrelated_repos(project):
    from meow.project.onboarding import protect_generated

    (project / ".gitignore").write_text("", encoding="utf-8")
    assert protect_generated(project) == []
    assert (project / ".gitignore").read_text(encoding="utf-8") == ""


def test_generated_problems_reports_without_writing(project):
    from meow.project.onboarding import generated_problems

    write(project / GENERATED, "x")
    (project / ".gitignore").write_text("", encoding="utf-8")
    assert generated_problems(project)
    assert (project / ".gitignore").read_text(encoding="utf-8") == ""


def test_commit_worktree_never_commits_generated_skills(project):
    from meow.execution.delivery import commit_worktree

    (project / ".gitignore").write_text("", encoding="utf-8")
    write(project / GENERATED, "x")
    write(project / "real.txt", "y")
    commit_worktree(project, ["-m", "work"])
    tracked = _git(project, "ls-files").stdout.split()
    assert "real.txt" in tracked
    assert GENERATED not in tracked


def _local_copy(project: Path) -> str:
    rel = ".claude/skills/mine/SKILL.md"
    write(project / rel, "---\nname: mine\ndescription: Mine.\n---\n")
    write(project / ".claude/skills/mine/.meow-managed", "local\n")
    return rel


def test_published_local_skill_is_untracked_and_never_committed(project):
    from meow.execution.delivery import commit_worktree
    from meow.project.onboarding import protect_generated

    rel = _local_copy(project)
    _git(project, "add", "-f", ".claude/skills/mine")
    assert any("stopped tracking" in line for line in protect_generated(project))
    assert _git(project, "ls-files", rel).stdout == ""
    assert _git(project, "check-ignore", "-q", rel).returncode == 0

    (project / ".git" / "info" / "exclude").write_text("", encoding="utf-8")
    write(project / "real.txt", "y")
    commit_worktree(project, ["-m", "work"])
    tracked = _git(project, "ls-files").stdout.split()
    assert "real.txt" in tracked
    assert rel not in tracked


def test_shared_project_skill_is_left_tracked(project):
    from meow.project.onboarding import protect_generated

    rel = ".claude/skills/team/SKILL.md"
    write(project / rel, "x")
    write(project / ".claude/skills/team/.meow-managed", "project\n")
    _git(project, "add", "-f", ".claude/skills/team")
    assert protect_generated(project) == []
    assert _git(project, "ls-files", rel).stdout.strip() == rel


def test_check_reports_local_skill_missing_from_exclude(project):
    from meow.project.onboarding import generated_problems, protect_generated

    _local_copy(project)
    (project / ".git" / "info" / "exclude").write_text("", encoding="utf-8")
    assert any("not ignored" in line for line in generated_problems(project))
    protect_generated(project)
    assert not any("not ignored" in line for line in generated_problems(project))


def test_flat_local_skill_does_not_rewrite_gitignore(project):
    from meow.project.onboarding import protect_generated

    _local_copy(project)
    (project / ".gitignore").write_text(".meow/\n", encoding="utf-8")
    protect_generated(project)
    assert (project / ".gitignore").read_text(encoding="utf-8") == ".meow/\n"
