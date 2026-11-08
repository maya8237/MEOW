"""Standalone rendering of agents for use outside a MEOW run (`claude_agent`)."""

import pytest

from meow.project.config import load_config
from meow.project.custom.rendering import builtin_agent
from meow.project.custom.standalone import render_standalone, standalone_problems


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
    return root


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def agent(project, name, text, suffix="toml"):
    write(project / ".meow" / "agents" / f"{name}.{suffix}", text)
    return load_config(project)["customizations"].agent(name)


def test_claude_agent_defaults_to_off(project, home):
    found = agent(project, "a", 'description = "d"\nprompt = "p"\n')

    assert found.claude_agent is False


@pytest.mark.parametrize(
    "toml, md",
    [("true", "true"), ("false", "false")],
)
def test_claude_agent_key_reads_in_both_formats(project, home, toml, md):
    from_toml = agent(
        project, "a", f'description = "d"\nprompt = "p"\nclaude_agent = {toml}\n'
    )
    from_md = agent(
        project,
        "b",
        f"---\ndescription: d\nmeow-claude-agent: {md}\n---\np\n",
        "md",
    )

    assert from_toml.claude_agent is from_md.claude_agent is (toml == "true")


def test_claude_agent_must_be_a_boolean(project, home):
    write(
        project / ".meow" / "agents" / "a.toml",
        'description = "d"\nprompt = "p"\nclaude_agent = "yes"\n',
    )

    with pytest.raises(ValueError, match="claude_agent"):
        load_config(project)


def test_active_dir_renders_as_the_current_directory(project, home):
    found = agent(
        project,
        "scout",
        'description = "d"\nprompt = "Search {{active_dir}} only."\n',
    )

    assert standalone_problems(found) == []
    assert render_standalone(found) == "Search the current working directory only."


def test_unset_conditions_are_false_and_fragments_are_inlined(project, home):
    found = agent(
        project,
        "scout",
        "description = 'd'\n"
        "prompt = '''\nStart.\n{{#if focus}}\nFocus on {{focus}}.\n{{/if}}\n"
        "{{> tail}}\n'''\n"
        "[fragments]\ntail = 'Be brief.'\n",
    )

    assert render_standalone(found) == "Start. Be brief."


def test_run_only_values_block_the_agent_and_are_named(project, home):
    found = agent(
        project,
        "grader",
        'description = "d"\nprompt = "Read {{plan_file}}. {{verdict_format}}"\n',
    )

    problems = standalone_problems(found)

    assert [p for p in problems if "plan_file" in p]
    assert [p for p in problems if "verdict_format" in p]
    with pytest.raises(ValueError, match="plan_file"):
        render_standalone(found)


def test_a_dotted_run_value_blocks_the_agent(project, home):
    found = agent(
        project,
        "grader",
        'description = "d"\nprompt = "Review {{inputs.topic}}."\n',
    )

    assert any("inputs" in problem for problem in standalone_problems(found))


def test_standalone_variant_wins_over_the_main_prompt(project, home):
    found = agent(
        project,
        "grader",
        "description = 'd'\nprompt = 'Read {{plan_file}}.'\n"
        "[variants]\nstandalone = 'Review the working tree.'\n",
    )

    assert standalone_problems(found) == []
    assert render_standalone(found) == "Review the working tree."


def test_opted_in_agent_that_cannot_render_fails_config_load(project, home):
    path = write(
        project / ".meow" / "agents" / "grader.toml",
        'description = "d"\nprompt = "Read {{plan_file}}."\nclaude_agent = true\n',
    )

    with pytest.raises(ValueError, match="plan_file") as error:
        load_config(project)

    assert str(path) in str(error.value)


def test_agents_that_did_not_opt_in_are_not_checked(project, home):
    agent(project, "grader", 'description = "d"\nprompt = "Read {{plan_file}}."\n')


def test_markdown_agent_that_cannot_render_fails_when_opted_in(project, home):
    write(
        project / ".meow" / "agents" / "grader.md",
        "---\ndescription: d\nmeow-claude-agent: true\n---\nRead {{plan_file}}.\n",
    )

    with pytest.raises(ValueError, match="plan_file"):
        load_config(project)


# Which shipped agents work outside a MEOW run today. A prompt change that moves
# an agent across this line should be a deliberate edit to this list.
RENDERS_STANDALONE = {"explorer", "lint-fixer", "review-fixer"}


def test_builtin_standalone_support_is_pinned():
    from meow.project.custom import builtin_dir

    names = sorted(path.stem for path in (builtin_dir() / "agents").glob("*.toml"))
    renders = {name for name in names if not standalone_problems(builtin_agent(name))}

    assert renders == RENDERS_STANDALONE


def test_builtins_do_not_opt_in():
    from meow.project.custom import builtin_dir

    for path in (builtin_dir() / "agents").glob("*.toml"):
        assert builtin_agent(path.stem).claude_agent is False
