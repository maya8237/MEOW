"""Which agents sync to Claude Code when `claude_agent` is not set."""

import subprocess

import pytest

from meow.project.config import load_config
from meow.project.custom.agent_sync import sync_agents

PLAIN = 'description = "Scout."\ntools = ["Read"]\nprompt = "Search {{active_dir}}."\n'
NEEDS_RUN = 'description = "Run only."\nprompt = "Follow {{plan_file}}."\n'


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


def put(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def sync(project, home):
    custom = load_config(project)["customizations"]
    return sync_agents(custom.catalog, project, home / ".meow")


def test_your_agents_sync_at_every_scope_without_a_flag(project, home):
    put(project / ".meow" / "agents" / "p.toml", PLAIN)
    put(project / ".meow" / "local" / "agents" / "l.toml", PLAIN)
    put(home / ".meow" / "agents" / "u.toml", PLAIN)

    sync(project, home)

    assert (project / ".claude" / "agents" / "meow" / "p.md").is_file()
    assert (project / ".claude" / "agents" / "meow-local" / "l.md").is_file()
    assert (home / ".claude" / "agents" / "meow" / "u.md").is_file()


def test_markdown_agents_sync_without_a_flag(project, home):
    put(
        project / ".meow" / "agents" / "m.md",
        "---\nname: m\ndescription: Scout.\ntools: Read\n---\nSearch.\n",
    )

    sync(project, home)

    assert (project / ".claude" / "agents" / "meow" / "m.md").is_file()


def test_false_opts_out(project, home):
    put(project / ".meow" / "agents" / "p.toml", PLAIN + "claude_agent = false\n")

    assert sync(project, home) == []
    assert not (project / ".claude" / "agents").exists()


def test_default_agent_that_needs_run_values_is_skipped_quietly(project, home):
    put(project / ".meow" / "agents" / "r.toml", NEEDS_RUN)

    assert sync(project, home) == []
    assert not (project / ".claude" / "agents").exists()


def test_explicit_opt_in_that_needs_run_values_is_an_error(project, home):
    put(project / ".meow" / "agents" / "r.toml", NEEDS_RUN + "claude_agent = true\n")

    with pytest.raises(ValueError, match="plan_file"):
        load_config(project)


def test_builtins_and_ejected_builtins_stay_off(project, home):
    explorer = (
        'description = "d"\nejected_from = "builtin:explorer@abc"\n'
        'prompt = "Search {{active_dir}}."\n'
    )
    put(project / ".meow" / "agents" / "explorer.toml", explorer)

    assert sync(project, home) == []
    assert not (project / ".claude" / "agents").exists()


def test_ejected_builtin_syncs_when_it_opts_in(project, home):
    explorer = (
        'description = "d"\nejected_from = "builtin:explorer@abc"\n'
        'claude_agent = true\nprompt = "Search {{active_dir}}."\n'
    )
    put(project / ".meow" / "agents" / "explorer.toml", explorer)

    sync(project, home)

    assert (project / ".claude" / "agents" / "meow" / "explorer.md").is_file()
