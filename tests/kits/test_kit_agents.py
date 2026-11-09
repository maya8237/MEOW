"""Kit agents synced to Claude Code: tools are required, widening is held."""

from pathlib import Path

from meow.project.config import load_config
from meow.project.custom import frontmatter, kit_ops
from meow.project.custom.agent_sync import check_agent_registration, sync_agents


def agent(tools='["Read"]', prose="Search {{active_dir}}.", opt_in="true"):
    lines = ['description = "Scout."', f"claude_agent = {opt_in}"]
    if tools is not None:
        lines.append(f"tools = {tools}")
    return "\n".join([*lines, f'prompt = "{prose}"']) + "\n"


def add(project, user_dir, repo, **options):
    return kit_ops.install(
        project, user_dir, str(repo.path), output=lambda _: None, **options
    )


def sync(project, user_dir):
    custom = load_config(project)["customizations"]
    sync_agents(custom.catalog, project, user_dir)
    return check_agent_registration(custom.catalog, project, user_dir)


def copy(scope="user", project=None):
    base = Path.home() if scope == "user" else project
    return base / ".claude" / "agents" / "meow" / "scout.md"


def test_installed_kit_agent_is_synced_with_its_tools(make_kit, project, user_dir):
    add(project, user_dir, make_kit({"agents/scout.toml": agent()}))

    assert sync(project, user_dir) == []
    data, _ = frontmatter.parse(copy())

    assert data["tools"] == "Read"
    assert data["meow-managed"] == "user"


def test_kit_agent_without_tools_is_not_synced(make_kit, project, user_dir):
    add(project, user_dir, make_kit({"agents/scout.toml": agent(tools=None)}))

    problems = sync(project, user_dir)

    assert not copy().exists()
    assert any("scout" in line and "declare tools" in line for line in problems)


def test_kit_agent_that_did_not_opt_in_is_not_synced(make_kit, project, user_dir):
    add(project, user_dir, make_kit({"agents/scout.toml": agent(opt_in="false")}))

    assert sync(project, user_dir) == []
    assert not copy().exists()


def test_widened_tools_wait_for_accept(make_kit, project, user_dir):
    repo = make_kit({"agents/scout.toml": agent()})
    add(project, user_dir, repo)
    sync(project, user_dir)
    repo.write({"agents/scout.toml": agent(tools='["Read", "Bash"]')})

    lines = kit_ops.update(project, user_dir)
    problems = sync(project, user_dir)

    assert any("on hold" in line and "Bash" in line for line in lines)
    assert any(line.endswith("--accept") for line in lines)
    assert any("on hold" in line and "Bash" in line for line in problems)
    assert frontmatter.parse(copy())[0]["tools"] == "Read"  # old copy stays

    kit_ops.update(project, user_dir, accept=True)

    assert sync(project, user_dir) == []
    assert frontmatter.parse(copy())[0]["tools"] == "Read, Bash"


def test_narrowed_tools_and_prose_changes_apply_at_once(make_kit, project, user_dir):
    repo = make_kit({"agents/scout.toml": agent(tools='["Read", "Bash"]')})
    add(project, user_dir, repo)
    sync(project, user_dir)
    repo.write({"agents/scout.toml": agent(prose="Look in {{active_dir}}.")})

    kit_ops.update(project, user_dir)

    assert sync(project, user_dir) == []
    data, body = frontmatter.parse(copy())
    assert data["tools"] == "Read"
    assert body == "Look in the current working directory."


def test_a_new_opted_in_agent_in_an_update_is_held(make_kit, project, user_dir):
    repo = make_kit({"README.md": "x", "agents/other.toml": agent(opt_in="false")})
    add(project, user_dir, repo)
    repo.write({"agents/scout.toml": agent()})

    lines = kit_ops.update(project, user_dir)
    problems = sync(project, user_dir)

    assert any("scout" in line and "on hold" in line for line in lines)
    assert not copy().exists()
    assert any("scout" in line for line in problems)

    kit_ops.update(project, user_dir, accept=True)
    sync(project, user_dir)

    assert copy().is_file()


def test_auto_accept_applies_widening(make_kit, project, user_dir):
    repo = make_kit({"agents/scout.toml": agent()})
    add(project, user_dir, repo, auto_accept_hooks=True)
    repo.write({"agents/scout.toml": agent(tools='["Read", "Bash"]')})

    kit_ops.update(project, user_dir)
    sync(project, user_dir)

    assert frontmatter.parse(copy())[0]["tools"] == "Read, Bash"


def test_project_kits_are_trusted_because_the_pin_is_reviewed(
    make_kit, project, user_dir
):
    repo = make_kit({"agents/scout.toml": agent(tools='["Read", "Bash"]')})
    add(project, user_dir, repo, scope="project")

    assert sync(project, user_dir) == []
    data, _ = frontmatter.parse(copy("project", project))

    assert data["tools"] == "Read, Bash"
    assert data["meow-managed"] == "project"
