"""`meow agents eject NAME`: a builtin as a Claude Code Markdown agent."""

import hashlib

import pytest

from meow.cli.cli import cli_main
from meow.project.config import load_config
from meow.project.custom.rendering import builtin_agent
from meow.project.custom.sources import builtin_dir
from meow.project.custom.standalone import syncs

USAGE_ERROR = 2  # argparse's exit code for a rejected option
BUILTIN_AGENTS = 12
NAMES = sorted(path.stem for path in (builtin_dir() / "agents").glob("*.md"))
FIELDS = (
    "description",
    "model",
    "tools",
    "skills",
    "agents",
    "session",
    "lint_on_edit",
    "prompt",
    "variants",
    "fragments",
)


def command(tmp_path, *args):
    cli_main([*args, "--work-dir", str(tmp_path)])


def ejected(tmp_path, name):
    return tmp_path / ".meow" / "agents" / f"{name}.md"


def test_every_builtin_ships_as_markdown():
    assert len(NAMES) == BUILTIN_AGENTS
    assert not list((builtin_dir() / "agents").glob("*.toml"))


@pytest.mark.parametrize("name", NAMES)
def test_ejected_markdown_loads_like_the_builtin(tmp_path, name):
    command(tmp_path, "agents", "eject", name)

    found = load_config(tmp_path)["customizations"].agent(name)
    shipped = builtin_agent(name)

    assert found.file == ejected(tmp_path, name)
    assert found.source.scope == "project"
    assert all(getattr(found, f) == getattr(shipped, f) for f in FIELDS)
    assert found.claude_agent is None
    assert not syncs(found)


def test_origin_hash_is_the_shipped_file_and_drift_is_reported(tmp_path, capsys):
    command(tmp_path, "agents", "eject", "explorer")
    file = ejected(tmp_path, "explorer")
    origin = hashlib.sha256(
        (builtin_dir() / "agents" / "explorer.md").read_bytes()
    ).hexdigest()
    text = file.read_text(encoding="utf-8")

    assert f"meow-ejected-from: builtin:explorer@{origin}" in text
    assert "drift" not in capsys.readouterr().err.lower()
    file.write_text(text.replace(origin, "0" * 64), encoding="utf-8")
    command(tmp_path, "agents", "show", "explorer")

    assert "drift" in capsys.readouterr().err.lower()


@pytest.mark.parametrize("existing", ["explorer.md", "explorer.toml"])
def test_existing_definition_in_the_scope_is_never_overwritten(tmp_path, existing):
    folder = tmp_path / ".meow" / "agents"
    folder.mkdir(parents=True)
    (folder / existing).write_text("mine\n", encoding="utf-8")

    with pytest.raises(SystemExit) as error:
        command(tmp_path, "agents", "eject", "explorer")

    assert error.value.code == 1
    assert (folder / existing).read_text(encoding="utf-8") == "mine\n"


def test_scope_option_applies(tmp_path):
    command(tmp_path, "agents", "eject", "explorer", "--scope", "local")

    assert (tmp_path / ".meow" / "local" / "agents" / "explorer.md").is_file()


@pytest.mark.parametrize("kind", ["agents", "workflows"])
def test_there_is_no_format_option(tmp_path, kind):
    name = "explorer" if kind == "agents" else "quick"
    with pytest.raises(SystemExit) as error:
        command(tmp_path, kind, "eject", name, "--format", "md")

    assert error.value.code == USAGE_ERROR
