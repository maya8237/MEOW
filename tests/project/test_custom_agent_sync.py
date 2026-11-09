"""Syncing opted-in agents to Claude Code's subagent folders at every scope."""

import subprocess

import pytest

from meow.project.config import load_config
from meow.project.custom import frontmatter
from meow.project.custom.agent_sync import check_agent_registration, sync_agents
from meow.project.custom.registration import check_registration, sync_registration

ALLOWED = {
    "name",
    "description",
    "tools",
    "model",
    "skills",
    "meow-managed",
    "meow-source",
}
EXPLORER = """\
description = "Read-only scout: finds things."
model = "haiku"
tools = ["Read", "Grep"]
skills = ["superpowers:systematic-debugging"]
session = "persistent"
lint_on_edit = true
claude_agent = true
prompt = '''
Search {{active_dir}} and report file:line references.
'''
"""


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


def agent_file(project, name="scout", text=EXPLORER, scope="project"):
    base = project / ".meow" / ("local" if scope == "local" else "") / "agents"
    return write(base / f"{name}.toml", text)


def sync(project, home, scopes=("project", "local", "user")):
    custom = load_config(project)["customizations"]
    return sync_agents(custom.catalog, project, home / ".meow", scopes=scopes)


def check(project, home):
    custom = load_config(project)["customizations"]
    return check_agent_registration(custom.catalog, project, home / ".meow")


def copy(project, name="scout", folder="meow"):
    return project / ".claude" / "agents" / folder / f"{name}.md"


def test_project_agent_is_written_with_only_allowlisted_fields(project, home):
    agent_file(project)

    sync(project, home)
    data, body = frontmatter.parse(copy(project))

    assert set(data) == ALLOWED
    assert data["name"] == "scout"
    assert data["description"] == "Read-only scout: finds things."
    assert data["tools"] == "Read, Grep"
    assert data["model"] == "haiku"
    assert data["meow-managed"] == "project"
    assert data["meow-source"].startswith(".meow/agents/scout.toml@")
    assert (
        body == "Search the current working directory and report file:line references."
    )


def test_agents_that_opt_out_are_not_synced(project, home):
    agent_file(project, text=EXPLORER.replace("= true\nprompt", "= false\nprompt"))

    assert sync(project, home) == []
    assert not (project / ".claude" / "agents").exists()


def test_builtin_agents_are_not_synced(project, home):
    assert sync(project, home) == []
    assert not (project / ".claude" / "agents").exists()


def test_meow_only_settings_never_reach_the_copy(project, home):
    agent_file(project)

    sync(project, home)
    text = copy(project).read_text(encoding="utf-8")

    for word in ("session", "lint_on_edit", "claude_agent", "persistent"):
        assert word not in text


def test_custom_skill_names_become_plain_skill_names(project, home):
    write(
        project / ".meow" / "skills" / "triage" / "SKILL.md",
        "---\nname: triage\ndescription: d\n---\nbody\n",
    )
    agent_file(
        project,
        text=EXPLORER.replace("systematic-debugging", "x").replace(
            '["superpowers:x"]', '["triage", "superpowers:x"]'
        ),
    )

    sync(project, home)
    data, _ = frontmatter.parse(copy(project))

    assert data["skills"] == ["triage", "superpowers:x"]


def test_second_sync_changes_nothing(project, home):
    agent_file(project)
    sync(project, home)

    assert sync(project, home) == []
    assert check(project, home) == []


def test_source_change_updates_the_copy(project, home):
    path = agent_file(project)
    sync(project, home)
    path.write_text(EXPLORER.replace("file:line", "path:line"), encoding="utf-8")

    fixed = sync(project, home)

    assert fixed and "path:line" in copy(project).read_text(encoding="utf-8")


@pytest.mark.parametrize("how", ["deleted", "opted_out"])
def test_copy_is_removed_when_the_agent_stops_qualifying(project, home, how):
    path = agent_file(project)
    sync(project, home)
    if how == "deleted":
        path.unlink()
    else:
        path.write_text(EXPLORER.replace("claude_agent = true", "claude_agent = false"))

    fixed = sync(project, home)

    assert fixed and not copy(project).exists()


def test_hand_edited_copy_is_reported_then_replaced(project, home):
    agent_file(project)
    sync(project, home)
    copy(project).write_text(
        copy(project).read_text(encoding="utf-8") + "extra\n", encoding="utf-8"
    )

    assert any("out of date" in line for line in check(project, home))
    sync(project, home)

    assert "extra" not in copy(project).read_text(encoding="utf-8")


def test_a_users_file_at_the_target_is_never_overwritten(project, home):
    agent_file(project)
    mine = write(copy(project), "---\nname: mine\ndescription: d\n---\nmy prompt\n")

    problems = check(project, home)
    sync(project, home)

    assert any("MEOW did not" in line for line in problems)
    assert "my prompt" in mine.read_text(encoding="utf-8")


def test_a_users_agent_with_the_same_name_anywhere_blocks_the_sync(project, home):
    agent_file(project)
    mine = write(
        project / ".claude" / "agents" / "team" / "helpers" / "whatever.md",
        "---\nname: scout\ndescription: mine\n---\nmy prompt\n",
    )

    problems = check(project, home)
    sync(project, home)

    assert any("already defines" in line and str(mine) in line for line in problems)
    assert not copy(project).exists()
    assert "my prompt" in mine.read_text(encoding="utf-8")


def test_a_file_without_a_name_is_not_a_clash(project, home):
    agent_file(project)
    write(project / ".claude" / "agents" / "notes.md", "just documentation\n")

    sync(project, home)

    assert copy(project).is_file()


def test_local_agents_go_to_their_own_folder_and_are_excluded_from_git(project, home):
    agent_file(project, scope="local")

    sync(project, home)
    exclude = (project / ".git" / "info" / "exclude").read_text(encoding="utf-8")

    assert copy(project, folder="meow-local").is_file()
    assert not copy(project).exists()
    assert "/.claude/agents/meow-local/" in exclude
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=project,
        capture_output=True,
        text=True,
    ).stdout
    assert "meow-local" not in status


def test_local_exclude_rule_is_removed_with_the_last_local_agent(project, home):
    path = agent_file(project, scope="local")
    sync(project, home)
    path.unlink()

    sync(project, home)
    exclude = (project / ".git" / "info" / "exclude").read_text(encoding="utf-8")

    assert not copy(project, folder="meow-local").exists()
    assert "meow-local" not in exclude


def test_user_agents_go_to_the_users_claude_folder(project, home):
    write(home / ".meow" / "agents" / "scout.toml", EXPLORER)

    sync(project, home)

    assert (home / ".claude" / "agents" / "meow" / "scout.md").is_file()
    assert not copy(project).exists()


def test_scopes_limit_what_is_synced(project, home):
    agent_file(project)

    assert sync(project, home, scopes=("user",)) == []
    assert not copy(project).exists()


def test_check_changes_nothing(project, home):
    agent_file(project)

    problems = check(project, home)

    assert any("not synced" in line for line in problems)
    assert not (project / ".claude" / "agents").exists()


def test_generated_files_are_not_read_back_as_definitions(project, home):
    agent_file(project)
    sync(project, home)
    write(
        project / ".meow" / "agents" / "scout-copy.md",
        copy(project).read_text(encoding="utf-8"),
    )

    custom = load_config(project)["customizations"]

    assert {agent.name for agent in custom.catalog} >= {"scout"}
    assert "scout-copy" not in {agent.name for agent in custom.catalog}


def test_registration_covers_agents_with_skills_and_hooks(project, home):
    agent_file(project)
    custom = load_config(project)["customizations"]

    assert any(
        "scout" in line for line in check_registration(custom, project, home / ".meow")
    )
    sync_registration(custom, project, home / ".meow", ("project",))

    assert copy(project).is_file()


def test_agents_source_pointing_at_claude_agents_does_not_reimport_copies(
    project, home
):
    agent_file(project)
    sync(project, home)
    generated = copy(project).read_text(encoding="utf-8")
    write(project / ".claude" / "agents" / "scout.md", generated)  # top level too
    write(project / ".meow" / "config.toml", '[custom]\nagents = [".claude/agents"]\n')

    custom = load_config(project)["customizations"]

    assert [a.name for a in custom.catalog].count("scout") == 1
    assert custom.agent("scout").file.suffix == ".toml"
    assert sync(project, home) == []


def test_synced_agent_names_are_only_the_copies_meow_generated(project, home):
    from meow.project.custom.agent_sync import synced_agent_names

    agent_file(project)
    agent_file(project, "local-one", scope="local")
    write(home / ".meow" / "agents" / "mine.toml", EXPLORER)
    write(
        project / ".claude" / "agents" / "theirs.md",
        "---\nname: theirs\ndescription: d\n---\nbody\n",
    )
    sync(project, home)

    assert synced_agent_names(project) == {"scout", "local-one", "mine"}


def test_first_sync_into_a_new_agents_folder_says_to_restart_claude_code(project, home):
    agent_file(project)

    first = sync(project, home)

    assert any("/reload-plugins" in line for line in first)


def test_later_syncs_and_existing_folders_do_not_repeat_the_restart_hint(project, home):
    write(project / ".claude" / "agents" / "mine.md", "---\nname: mine\n---\nx\n")
    agent_file(project)

    assert not any("restart" in line for line in sync(project, home))

    (project / ".meow" / "agents" / "scout.toml").write_text(
        EXPLORER.replace("file:line", "path:line"), encoding="utf-8"
    )
    assert not any("restart" in line for line in sync(project, home))
