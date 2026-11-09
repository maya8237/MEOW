"""Edge cases of syncing agents to Claude Code: portability, hostile input, scopes."""

import subprocess

import pytest

from meow.project.config import load_config
from meow.project.custom import frontmatter
from meow.project.custom.agent_sync import check_agent_registration, sync_agents
from meow.project.custom.standalone import standalone_problems

BASE = 'description = "{d}"\nclaude_agent = true\ntools = ["Read"]\nprompt = "{p}"\n'


@pytest.fixture
def home(tmp_path, monkeypatch):
    user_home = tmp_path / "home"
    (user_home / ".meow").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.setenv("USERPROFILE", str(user_home))
    return user_home


def make_project(tmp_path, name="project"):
    root = tmp_path / name
    (root / ".meow").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


@pytest.fixture
def project(tmp_path, home):
    return make_project(tmp_path)


def write(path, text, newline=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline=newline) as handle:
        handle.write(text)
    return path


def sync(root, home):
    custom = load_config(root)["customizations"]
    picks = custom.claude_builtins
    fixed = sync_agents(custom.catalog, root, home / ".meow", picks=picks)
    return fixed, check_agent_registration(
        custom.catalog, root, home / ".meow", picks=picks
    )


def copy(root, name="scout", folder="meow"):
    return root / ".claude" / "agents" / folder / f"{name}.md"


def scout(root, text=None, newline=None):
    text = text or BASE.format(d="Scout.", p="Search {{active_dir}}.")
    return write(root / ".meow" / "agents" / "scout.toml", text, newline)


def test_copy_is_identical_on_every_machine(tmp_path, home):
    first = make_project(tmp_path, "one")
    second = make_project(tmp_path / "elsewhere" / "deeper", "two")
    scout(first)
    scout(second)

    sync(first, home)
    sync(second, home)

    assert copy(first).read_text(encoding="utf-8") == copy(second).read_text(
        encoding="utf-8"
    )


def test_line_endings_of_the_source_do_not_change_the_copy(tmp_path, home):
    lf = make_project(tmp_path, "lf")
    crlf = make_project(tmp_path, "crlf")
    text = BASE.format(d="Scout.", p="Search {{active_dir}}.")
    scout(lf, text)
    scout(crlf, text.replace("\n", "\r\n"), newline="")

    sync(lf, home)
    sync(crlf, home)

    assert copy(lf).read_text(encoding="utf-8") == copy(crlf).read_text(
        encoding="utf-8"
    )


def test_an_agent_from_outside_the_project_leaks_no_absolute_path(tmp_path, home):
    root = make_project(tmp_path)
    shared = tmp_path / "shared-agents"
    write(shared / "scout.toml", BASE.format(d="Scout.", p="Search {{active_dir}}."))
    write(
        root / ".meow" / "config.local.toml",
        f'[custom]\nagents = ["{shared.as_posix()}"]\n',
    )

    sync(root, home)
    text = copy(root, folder="meow-local").read_text(encoding="utf-8")

    assert str(tmp_path) not in text
    assert "scout.toml@" in text


def test_project_scope_config_cannot_reach_outside_the_repository(tmp_path, home):
    root = make_project(tmp_path)
    write(root / ".meow" / "config.toml", '[custom]\nagents = ["../elsewhere"]\n')

    with pytest.raises(ValueError, match="inside it"):
        load_config(root)


def test_hostile_description_round_trips(project, home):
    description = 'He said "hi": a --- b, \\ back # not a comment é 日本'
    scout(
        project,
        BASE.format(d=description.replace("\\", "\\\\").replace('"', '\\"'), p="p"),
    )

    sync(project, home)
    data, _ = frontmatter.parse(copy(project))

    assert data["description"] == description
    assert set(data) == {
        "name", "description", "tools", "meow-managed", "meow-source",
    }  # fmt: skip


def test_prompt_containing_a_fence_line_survives(project, home):
    scout(
        project,
        "description = 'd'\nclaude_agent = true\ntools = ['Read']\n"
        "prompt = '''\nBefore.\n\n---\n\nAfter.\n'''\n",
    )

    sync(project, home)
    _, body = frontmatter.parse(copy(project))

    assert body == "Before.\n\n---\n\nAfter."


def test_a_copy_with_invalid_utf8_is_replaced_not_a_crash(project, home):
    scout(project)
    sync(project, home)
    target = copy(project)
    target.write_bytes(target.read_bytes() + b"\xff\xfe junk")

    fixed, remaining = sync(project, home)

    assert fixed and remaining == []
    assert b"\xff" not in target.read_bytes()


def test_a_binary_file_in_the_way_is_left_alone(project, home):
    scout(project)
    blocker = copy(project)
    blocker.parent.mkdir(parents=True)
    blocker.write_bytes(b"\xff\xfe\x00 not text")

    _, remaining = sync(project, home)

    assert any("not MEOW's copy" in line for line in remaining)
    assert blocker.read_bytes() == b"\xff\xfe\x00 not text"


def test_project_that_is_the_home_directory_does_not_fight_the_user_scope(
    tmp_path, home
):
    (home / ".meow").mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=home, check=True)
    scout(home)

    _, remaining = sync(home, home)
    second, _ = sync(home, home)

    assert remaining == []
    assert second == []
    assert copy(home).is_file()


def test_comma_in_a_tool_name_fails_config_load_for_opted_in_agents(project, home):
    scout(
        project,
        "description = 'd'\nclaude_agent = true\n"
        "tools = ['Bash(git log:*, git diff:*)']\nprompt = 'p'\n",
    )

    with pytest.raises(ValueError, match="comma"):
        load_config(project)


def test_comma_in_a_tool_name_is_fine_for_agents_that_did_not_opt_in(project, home):
    scout(
        project,
        "description = 'd'\ntools = ['Bash(git log:*, git diff:*)']\nprompt = 'p'\n",
    )

    custom = load_config(project)["customizations"]

    assert standalone_problems(custom.agent("scout"))  # would block if opted in
    assert sync(project, home)[0] == []


def test_listing_the_same_builtin_in_two_configs_syncs_both_scopes(project, home):
    write(project / ".meow" / "config.toml", '[claude]\nagents = ["explorer"]\n')
    write(home / ".meow" / "config.toml", '[claude]\nagents = ["explorer"]\n')

    sync(project, home)

    assert copy(project, "explorer").is_file()
    assert (home / ".claude" / "agents" / "meow" / "explorer.md").is_file()


def test_config_error_names_the_scope_of_the_layer(project, home):
    write(home / ".meow" / "config.toml", '[claude]\nagents = ["nonesuch"]\n')

    with pytest.raises(ValueError, match="user config") as error:
        load_config(project)

    assert "nonesuch" in str(error.value)


def test_a_crlf_checkout_of_a_committed_copy_is_still_ours(project, home):
    """Windows with autocrlf checks the committed copy out with CRLF endings."""
    scout(project)
    sync(project, home)
    target = copy(project)
    target.write_bytes(target.read_bytes().replace(b"\n", b"\r\n"))

    fixed, remaining = sync(project, home)

    assert remaining == [] and fixed == []
    assert b"\r\n" in target.read_bytes()  # left exactly as checked out


def test_a_crlf_copy_that_was_hand_edited_is_reported_then_replaced(project, home):
    scout(project)
    sync(project, home)
    target = copy(project)
    target.write_bytes(target.read_bytes().replace(b"\n", b"\r\n") + b"extra\r\n")

    _, remaining = sync(project, home)  # sync repaired it; check ran afterwards
    fixed, _ = sync(project, home)

    assert fixed == [] and b"extra" not in target.read_bytes()
    assert remaining == []
