import asyncio
import hashlib
from unittest.mock import AsyncMock, patch

import pytest

from meow.cli.cli import cli_main
from meow.cli.dispatch import _dispatch_plain_build
from meow.cli.parser import _build_arg_parser
from meow.execution.run_state import RunStore
from meow.execution.sprint_runner import run_sprint
from meow.project.config import load_config
from meow.project.custom.sources import builtin_dir


def test_workflow_and_inputs_reach_run_sprint(tmp_path):
    args = _build_arg_parser().parse_args([
        "run",
        "Audit",
        "-n",
        "--workflow",
        "review-and-fix",
        "--input",
        "topic=a=b",
        "--input",
        "severity=high",
    ])
    with patch("meow.cli.dispatch.run_sprint", new=AsyncMock()) as run:
        _dispatch_plain_build(args, tmp_path, use_worktree=False)
    assert run.await_args.kwargs["workflow"] == "review-and-fix"
    assert run.await_args.kwargs["inputs"] == {"topic": "a=b", "severity": "high"}


def test_unknown_workflow_fails_before_creating_record(tmp_path):
    with pytest.raises(ValueError, match="no workflow"):
        asyncio.run(
            run_sprint(tmp_path, None, "Audit", use_worktree=False, workflow="missing")
        )
    assert not RunStore(tmp_path).record_paths()


def command(tmp_path, *args):
    cli_main([*args, "--work-dir", str(tmp_path)])


def test_catalog_lists_shipped_workflows_and_shows_resolved_override(tmp_path, capsys):
    command(tmp_path, "workflows", "list")
    output = capsys.readouterr().out
    assert all(
        name in output for name in ("default", "quick", "review-and-fix", "test-fix")
    )
    directory = tmp_path / ".meow" / "workflows"
    directory.mkdir(parents=True)
    (directory / "quick.toml").write_text(
        'description="My quick"\nsteps=[]\n', encoding="utf-8"
    )
    command(tmp_path, "workflows", "show", "quick")
    assert "My quick" in capsys.readouterr().out
    command(tmp_path, "workflows", "validate")
    assert "quick: valid" in capsys.readouterr().out


@pytest.mark.parametrize("kind,name", [("agents", "planner"), ("workflows", "quick")])
def test_eject_stamps_origin_refuses_overwrite_and_warns_on_drift(
    tmp_path, capsys, kind, name
):
    command(tmp_path, kind, "eject", name, "--scope", "local")
    file = tmp_path / ".meow" / "local" / kind / f"{name}.toml"
    origin = hashlib.sha256(
        (builtin_dir() / kind / f"{name}.toml").read_bytes()
    ).hexdigest()
    text = file.read_text(encoding="utf-8")
    assert f"builtin:{name}@{origin}" in text
    with pytest.raises(SystemExit) as error:
        command(tmp_path, kind, "eject", name, "--scope", "local")
    assert error.value.code == 1
    assert file.read_text(encoding="utf-8") == text
    file.write_text(text.replace(origin, "0" * 64), encoding="utf-8")
    command(tmp_path, kind, "show", name)
    assert "drift" in capsys.readouterr().err.lower()
    if kind == "workflows":
        command(tmp_path, kind, "validate", name)
        assert "drift" in capsys.readouterr().err.lower()


def test_agents_list_and_diff(tmp_path, capsys):
    command(tmp_path, "agents", "list")
    assert "explorer (builtin)" in capsys.readouterr().out
    command(tmp_path, "agents", "eject", "explorer")
    file = tmp_path / ".meow" / "agents" / "explorer.toml"
    file.write_text(
        file.read_text().replace('model = "haiku"', 'model = "sonnet"'),
        encoding="utf-8",
    )
    command(tmp_path, "agents", "diff", "explorer")
    assert '+model = "sonnet"' in capsys.readouterr().out


@pytest.mark.parametrize(
    "table", ['workflow="x"', "[workflow]\ndefault=3", '[workflow]\nother="quick"']
)
def test_workflow_config_rejects_malformed_selection(tmp_path, table):
    directory = tmp_path / ".meow"
    directory.mkdir()
    (directory / "config.toml").write_text(table, encoding="utf-8")
    with pytest.raises(ValueError, match="workflow"):
        load_config(tmp_path)


def test_config_default_selects_workflow_and_inputs_are_retained_for_resume(tmp_path):
    directory = tmp_path / ".meow" / "workflows"
    directory.mkdir(parents=True)
    (directory / "empty.toml").write_text(
        'description="Empty"\nsteps=[]', encoding="utf-8"
    )
    (directory.parent / "config.toml").write_text(
        '[workflow]\ndefault="empty"', encoding="utf-8"
    )
    asyncio.run(
        run_sprint(tmp_path, None, "Audit", use_worktree=False, inputs={"topic": "API"})
    )
    saved = RunStore(tmp_path).latest().results["workflow"]
    assert saved["name"] == "empty"
    assert saved["inputs"] == {"topic": "API"}
