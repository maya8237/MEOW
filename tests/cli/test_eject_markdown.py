"""`meow agents eject NAME --format md`: a builtin as a Claude Code Markdown agent."""

import hashlib

import pytest

from meow.cli.cli import cli_main
from meow.project.config import load_config
from meow.project.custom.rendering import builtin_agent
from meow.project.custom.sources import builtin_dir
from meow.project.custom.standalone import syncs

USAGE_ERROR = 2  # argparse's exit code for a rejected option
NAMES = sorted(path.stem for path in (builtin_dir() / "agents").glob("*.toml"))
PORTABLE = [
    n for n in NAMES if not (builtin_agent(n).variants or builtin_agent(n).fragments)
]
FIELDS = (
    "description",
    "model",
    "tools",
    "skills",
    "agents",
    "session",
    "lint_on_edit",
)


def command(tmp_path, *args):
    cli_main([*args, "--work-dir", str(tmp_path)])


def ejected(tmp_path, name):
    return tmp_path / ".meow" / "agents" / f"{name}.md"


def test_there_are_agents_in_both_groups():
    assert "explorer" in PORTABLE and {"planner", "reviewer"} <= set(NAMES) - set(
        PORTABLE
    )


@pytest.mark.parametrize("name", PORTABLE)
def test_ejected_markdown_loads_like_the_builtin(tmp_path, name):
    command(tmp_path, "agents", "eject", name, "--format", "md")

    found = load_config(tmp_path)["customizations"].agent(name)
    shipped = builtin_agent(name)

    assert found.file == ejected(tmp_path, name)
    assert found.source.scope == "project"
    assert all(getattr(found, f) == getattr(shipped, f) for f in FIELDS)
    assert found.prompt.strip() == shipped.prompt.strip()
    assert found.claude_agent is None
    assert not syncs(found)


@pytest.mark.parametrize("name", ["planner", "reviewer"])
def test_agents_with_variants_or_fragments_are_refused(tmp_path, name):
    with pytest.raises(SystemExit) as error:
        command(tmp_path, "agents", "eject", name, "--format", "md")

    assert error.value.code == 1
    assert not ejected(tmp_path, name).exists()


def test_refusal_names_what_blocks_it(tmp_path, capsys):
    with pytest.raises(SystemExit):
        command(tmp_path, "agents", "eject", "reviewer", "--format", "md")

    err = capsys.readouterr().err
    assert "reviewer" in err and "variants" in err and "TOML" in err


def test_origin_hash_is_the_shipped_toml_and_drift_is_reported(tmp_path, capsys):
    command(tmp_path, "agents", "eject", "explorer", "--format", "md")
    file = ejected(tmp_path, "explorer")
    origin = hashlib.sha256(
        (builtin_dir() / "agents" / "explorer.toml").read_bytes()
    ).hexdigest()
    text = file.read_text(encoding="utf-8")

    assert f"meow-ejected-from: builtin:explorer@{origin}" in text
    assert "drift" not in capsys.readouterr().err.lower()
    file.write_text(text.replace(origin, "0" * 64), encoding="utf-8")
    command(tmp_path, "agents", "show", "explorer")

    assert "drift" in capsys.readouterr().err.lower()


def test_existing_definition_in_the_scope_is_never_overwritten(tmp_path):
    command(tmp_path, "agents", "eject", "explorer")  # the TOML copy
    toml = tmp_path / ".meow" / "agents" / "explorer.toml"
    before = toml.read_text(encoding="utf-8")

    with pytest.raises(SystemExit) as error:
        command(tmp_path, "agents", "eject", "explorer", "--format", "md")

    assert error.value.code == 1
    assert not ejected(tmp_path, "explorer").exists()
    assert toml.read_text(encoding="utf-8") == before


def test_markdown_copy_blocks_a_later_toml_eject(tmp_path):
    command(tmp_path, "agents", "eject", "explorer", "--format", "md")

    with pytest.raises(SystemExit):
        command(tmp_path, "agents", "eject", "explorer")

    assert not (tmp_path / ".meow" / "agents" / "explorer.toml").exists()


def test_scope_option_applies(tmp_path):
    command(
        tmp_path, "agents", "eject", "explorer", "--format", "md", "--scope", "local"
    )

    assert (tmp_path / ".meow" / "local" / "agents" / "explorer.md").is_file()


def test_default_format_is_still_toml(tmp_path):
    command(tmp_path, "agents", "eject", "explorer")

    assert (tmp_path / ".meow" / "agents" / "explorer.toml").is_file()
    assert not ejected(tmp_path, "explorer").exists()


def test_workflows_have_no_format_option(tmp_path):
    with pytest.raises(SystemExit) as error:
        command(tmp_path, "workflows", "eject", "quick", "--format", "md")

    assert error.value.code == USAGE_ERROR
