"""Upgrade diagnostics preserve custom files and run valid workflows."""

import asyncio
import shutil

import pytest

from meow.cli.catalog_cli import catalog_command, eject
from meow.cli.parser import _build_arg_parser
from meow.execution.run_state import RunStore
from meow.execution.sprint_runner import run_sprint
from meow.project.custom import sources


@pytest.fixture
def installation(tmp_path, monkeypatch):
    shipped = tmp_path / "shipped"
    shutil.copytree(sources.builtin_dir(), shipped)
    monkeypatch.setattr(sources, "builtin_dir", lambda: shipped)
    project = tmp_path / "project"
    project.mkdir()
    return project, shipped


def empty_workflow(project, name="default"):
    directory = project / ".meow" / "workflows"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.toml"
    path.write_text('description="Custom"\nsteps=[]\n', encoding="utf-8")
    return path


def inspect_or_run(project, action):
    if action == "run":
        asyncio.run(run_sprint(project, None, "Audit", use_worktree=False))
        assert RunStore(project).latest().phase == "complete"
    else:
        args = _build_arg_parser().parse_args(["workflows", action, "default"])
        catalog_command(args, project)


@pytest.mark.parametrize("action", ["show", "validate", "run"])
def test_changed_workflow_warns_without_modifying_or_blocking_copy(
    installation, capsys, action
):
    project, shipped = installation
    path = eject("workflows", "default", "project", project)
    origin = path.read_text(encoding="utf-8").splitlines()[0]
    path.write_text(f'{origin}\ndescription="Custom"\nsteps=[]\n', encoding="utf-8")
    original = path.read_bytes()
    builtin = shipped / "workflows" / "default.toml"
    builtin.write_bytes(builtin.read_bytes() + b"\n# new release\n")

    inspect_or_run(project, action)

    warning = capsys.readouterr().err
    assert "Drift:" in warning
    assert str(path) in warning
    assert str(builtin) in warning
    assert warning.count("Drift:") == 1
    assert path.read_bytes() == original


@pytest.mark.parametrize("action", ["show", "validate", "run"])
def test_removed_agent_warns_and_keeps_valid_custom_definition(
    installation, capsys, action
):
    project, shipped = installation
    empty_workflow(project)
    path = eject("agents", "planner", "project", project)
    original = path.read_bytes()
    (shipped / "agents" / "planner.toml").unlink()

    inspect_or_run(project, action)

    warning = capsys.readouterr().err
    assert str(path) in warning
    assert "removed" in warning
    assert "not used by workflow 'default'" in warning
    assert path.read_bytes() == original


def test_removed_stage_warns_even_when_its_agent_definition_is_unchanged(
    installation, capsys
):
    project, shipped = installation
    path = eject("agents", "planner", "project", project)
    (shipped / "workflows" / "default.toml").write_text(
        'description="Upgraded default"\nsteps=[]\n', encoding="utf-8"
    )

    inspect_or_run(project, "run")

    warning = capsys.readouterr().err
    assert str(path) in warning
    assert "not used by workflow 'default'" in warning
    assert "another workflow" in warning
    assert "was removed" not in warning


@pytest.mark.parametrize("target", ["builtin.plan", "builtin.tasks", "builtin.deliver"])
def test_indirectly_used_agents_are_not_reported_unused(installation, capsys, target):
    project, _shipped = installation
    names = (
        ("lint-fixer", "review-fixer")
        if target == "builtin.deliver"
        else ("planner", "explorer")
    )
    for name in names:
        eject("agents", name, "project", project)
    path = empty_workflow(project)
    path.write_text(
        f'description="Custom"\n[[steps]]\nuses="{target}"\n', encoding="utf-8"
    )

    inspect_or_run(project, "validate")

    warning = capsys.readouterr().err
    assert "Unused:" not in warning
    assert "Drift:" not in warning


def test_called_workflow_and_agent_delegate_drift_are_reported(installation, capsys):
    project, shipped = installation
    workflow = eject("workflows", "quick", "project", project)
    agent = eject("agents", "explorer", "project", project)
    for path in (workflow, agent):
        builtin = shipped / path.parent.name / path.name
        builtin.write_bytes(builtin.read_bytes() + b"\n# upgraded\n")
    path = empty_workflow(project)
    path.write_text('description="Custom"\n[[steps]]\ncall="quick"\n', encoding="utf-8")

    inspect_or_run(project, "show")

    warning = capsys.readouterr().err
    assert str(workflow) in warning
    assert str(agent) in warning
    assert "not used" not in warning


def test_unchanged_and_handwritten_definitions_do_not_warn(installation, capsys):
    project, _shipped = installation
    path = eject("agents", "planner", "project", project)
    inspect_or_run(project, "validate")
    assert capsys.readouterr().err == ""
    # A handwritten definition has no provenance from which to infer drift.
    path.write_text('description="Custom"\nprompt="Plan"\n', encoding="utf-8")
    empty_workflow(project)
    inspect_or_run(project, "run")
    assert capsys.readouterr().err == ""


def test_removed_workflow_origin_does_not_remove_custom_workflow(installation, capsys):
    project, shipped = installation
    path = eject("workflows", "default", "project", project)
    origin = path.read_text(encoding="utf-8").splitlines()[0]
    path.write_text(f'{origin}\ndescription="Custom"\nsteps=[]\n', encoding="utf-8")
    (shipped / "workflows" / "default.toml").unlink()

    inspect_or_run(project, "run")

    warning = capsys.readouterr().err
    assert str(path) in warning
    assert "was removed" in warning
    assert path.exists()


def test_removed_builtin_agent_can_still_be_used_as_custom_agent(installation, capsys):
    project, shipped = installation
    agent = eject("agents", "planner", "project", project)
    (shipped / "agents" / "planner.toml").unlink()
    path = empty_workflow(project)
    path.write_text(
        'description="Custom"\n[[steps]]\nid="planning"\nagent="planner"\n',
        encoding="utf-8",
    )

    inspect_or_run(project, "validate")

    output = capsys.readouterr()
    assert "default: valid" in output.out
    assert str(agent) in output.err
    assert "was removed" in output.err
    assert "Unused:" not in output.err


def test_broken_workflow_remains_inspectable_without_claiming_agents_unused(
    installation, capsys
):
    project, _shipped = installation
    eject("agents", "planner", "project", project)
    path = empty_workflow(project)
    path.write_text(
        'description="Custom"\n[[steps]]\ncall="missing"\n', encoding="utf-8"
    )

    inspect_or_run(project, "show")

    output = capsys.readouterr()
    assert 'call="missing"' in output.out
    assert "Unused:" not in output.err


@pytest.mark.parametrize("kind,name", [("agents", "planner"), ("workflows", "quick")])
def test_renamed_ejected_definition_compares_its_recorded_origin(
    installation, capsys, kind, name
):
    project, shipped = installation
    path = eject(kind, name, "project", project).rename(
        project / ".meow" / kind / "personal.toml"
    )
    workflow = empty_workflow(project)
    step = 'id="personal"\nagent="personal"' if kind == "agents" else 'call="personal"'
    workflow.write_text(f'description="Custom"\n[[steps]]\n{step}\n', encoding="utf-8")

    inspect_or_run(project, "show")
    assert capsys.readouterr().err == ""
    origin = shipped / kind / f"{name}.toml"
    origin.write_bytes(origin.read_bytes() + b"\n# upgraded\n")

    inspect_or_run(project, "show")

    warning = capsys.readouterr().err
    assert str(path) in warning
    assert str(origin) in warning
    assert "was removed" not in warning


@pytest.mark.parametrize(
    "step,missing",
    [
        ('id="old"\nagent="removed-agent"', "removed-agent"),
        ('uses="builtin.removed"', "builtin.removed"),
        ('call="removed-workflow"', "removed-workflow"),
    ],
)
def test_missing_reference_stops_before_creating_run(installation, step, missing):
    project, _shipped = installation
    path = empty_workflow(project)
    path.write_text(f'description="Custom"\n[[steps]]\n{step}\n', encoding="utf-8")

    with pytest.raises(ValueError, match=missing) as error:
        asyncio.run(run_sprint(project, None, "Audit", use_worktree=False))

    assert str(path) in str(error.value)
    assert not RunStore(project).record_paths()
