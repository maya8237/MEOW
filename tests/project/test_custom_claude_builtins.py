"""`[claude] agents`: builtin agents synced to Claude Code, per config scope."""

import subprocess

import pytest

from meow.project.config import load_config
from meow.project.custom import frontmatter
from meow.project.custom.agent_sync import check_agent_registration, sync_agents


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
    return root


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def picks(project, names, where="project"):
    listing = ", ".join(f'"{name}"' for name in names)
    path = {
        "project": project / ".meow" / "config.toml",
        "local": project / ".meow" / "config.local.toml",
    }[where]
    return write(path, f"[claude]\nagents = [{listing}]\n")


def sync(project, home):
    custom = load_config(project)["customizations"]
    return sync_agents(
        custom.catalog, project, home / ".meow", picks=custom.claude_builtins
    )


def test_listed_builtin_is_written_to_the_scope_of_its_config(project, home):
    picks(project, ["explorer"])

    sync(project, home)
    target = project / ".claude" / "agents" / "meow" / "explorer.md"
    data, body = frontmatter.parse(target)

    assert data["name"] == "explorer"
    assert data["model"] == "haiku"
    assert data["tools"] == "Read, Grep, Glob, Bash"
    assert data["meow-source"].startswith("builtin:explorer@")
    assert data["meow-managed"] == "project"
    assert "the current working directory" in body
    assert "{{" not in body


def test_local_config_writes_the_local_folder(project, home):
    picks(project, ["explorer"], where="local")

    sync(project, home)

    assert (project / ".claude/agents/meow-local/explorer.md").is_file()
    assert not (project / ".claude/agents/meow/explorer.md").exists()
    exclude = (project / ".git/info/exclude").read_text(encoding="utf-8")
    assert "/.claude/agents/meow-local/" in exclude


def test_user_config_writes_the_users_folder(project, home):
    write(home / ".meow" / "config.toml", '[claude]\nagents = ["explorer"]\n')

    sync(project, home)

    assert (home / ".claude/agents/meow/explorer.md").is_file()
    assert not (project / ".claude/agents/meow/explorer.md").exists()


def test_nothing_is_synced_unless_listed(project, home):
    write(project / ".meow" / "config.toml", "")

    assert sync(project, home) == []
    assert not (project / ".claude").exists()


def test_unlisting_removes_the_copy(project, home):
    config = picks(project, ["explorer"])
    sync(project, home)
    config.write_text("[claude]\nagents = []\n", encoding="utf-8")

    sync(project, home)

    assert not (project / ".claude/agents/meow/explorer.md").exists()


def test_check_reports_a_listed_builtin_that_is_not_synced(project, home):
    picks(project, ["explorer"])
    custom = load_config(project)["customizations"]

    problems = check_agent_registration(
        custom.catalog, project, home / ".meow", picks=custom.claude_builtins
    )

    assert any("explorer" in line and "not synced" in line for line in problems)


def test_unknown_builtin_names_the_known_ones(project, home):
    picks(project, ["nonesuch"])

    with pytest.raises(ValueError, match="nonesuch") as error:
        load_config(project)

    assert "explorer" in str(error.value)


def test_builtin_that_needs_run_values_fails_naming_the_value(project, home):
    picks(project, ["planner"])

    with pytest.raises(ValueError, match="plan_file") as error:
        load_config(project)

    assert "[claude]" in str(error.value)


@pytest.mark.parametrize(
    "text",
    ['[claude]\nagent = ["explorer"]\n', '[claude]\nagents = "explorer"\n'],
)
def test_claude_table_is_validated(project, home, text):
    write(project / ".meow" / "config.toml", text)

    with pytest.raises(ValueError, match=r"\[claude\]"):
        load_config(project)


def test_an_ejected_override_is_what_gets_synced(project, home):
    write(
        project / ".meow" / "agents" / "explorer.toml",
        'description = "My scout."\nprompt = "Look around {{active_dir}}."\n',
    )
    picks(project, ["explorer"])

    sync(project, home)
    data, body = frontmatter.parse(project / ".claude/agents/meow/explorer.md")

    assert data["description"] == "My scout."
    assert body == "Look around the current working directory."


def test_reviewer_can_be_listed_and_is_written_for_claude_code(project, home):
    picks(project, ["reviewer"])

    sync(project, home)
    data, body = frontmatter.parse(
        project / ".claude" / "agents" / "meow" / "reviewer.md"
    )

    assert data["name"] == "reviewer"
    assert "git diff" in body and "Sprint Contract" not in body


def test_synced_reviewer_cannot_write_but_the_run_reviewer_can(project, home):
    from meow.project.custom.rendering import builtin_agent

    picks(project, ["reviewer"])

    sync(project, home)
    data, _ = frontmatter.parse(project / ".claude" / "agents" / "meow" / "reviewer.md")

    assert data["tools"] == "Read, Grep, Glob, Bash"
    assert "Write" in builtin_agent("reviewer").tools
