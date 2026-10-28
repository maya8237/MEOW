"""Python preparation never invokes a real installer in these tests."""

import asyncio
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from meow.infrastructure import python_dependencies as python
from meow.infrastructure.worktree_setup import WorktreeSetupError, prepare_environment


@pytest.fixture
def installer(tmp_path, monkeypatch):
    monkeypatch.setattr(python.shutil, "which", lambda _name, **_kwargs: None)
    calls = []

    def run(argv, cwd, env, timeout):
        calls.append((argv, cwd, env, timeout))
        executable = python.venv_python(cwd)
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.touch()
        (cwd / ".venv/pyvenv.cfg").touch()
        return 0, "", False

    monkeypatch.setattr(python, "run_captured", run)
    return calls


def project(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname="example"\nversion="1"\n'
        '[build-system]\nrequires=["setuptools"]\n'
        '[project.optional-dependencies]\ndev=["pytest"]\n'
    )


def test_locked_uv_uses_local_environment(tmp_path, monkeypatch, installer):
    project(tmp_path)
    (tmp_path / "uv.lock").write_text("locked")
    monkeypatch.setattr(python.shutil, "which", lambda _name: "uv")
    python.ensure_python_dependencies(tmp_path)
    assert installer[0][0] == ["uv", "sync", "--locked", "--extra", "dev"]
    assert installer[0][2]["UV_PROJECT_ENVIRONMENT"] == str(tmp_path / ".venv")


def test_fallback_reuses_health_and_reinstalls_changed_manifest(tmp_path, installer):
    project(tmp_path)
    python.ensure_python_dependencies(tmp_path)
    python.ensure_python_dependencies(tmp_path)
    count = len(installer)
    assert count == len(("venv", "pip"))
    assert installer[-1][0][-2:] == ["-e", ".[dev]"]
    (tmp_path / "requirements.txt").write_text("pytest\n")
    python.ensure_python_dependencies(tmp_path)
    assert len(installer) > count
    python.venv_python(tmp_path).unlink()
    count = len(installer)
    python.ensure_python_dependencies(tmp_path)
    assert len(installer) > count


def test_requirements_only_and_paths_with_spaces(tmp_path, installer):
    root = tmp_path / "task with spaces"
    root.mkdir()
    (root / "requirements.txt").write_text("pytest\n")
    python.ensure_python_dependencies(root)
    assert installer[-1][0] == [
        str(python.venv_python(root)),
        "-m",
        "pip",
        "install",
        "--prefix",
        str(root / ".venv"),
        "--no-user",
        "-r",
        "requirements.txt",
    ]
    assert installer[-1][1] == root


def test_failure_stays_pending_and_retryable(tmp_path, monkeypatch, installer):
    project(tmp_path)
    successful = python.run_captured
    monkeypatch.setattr(python, "run_captured", lambda *_a: (1, "secret-token", False))
    with pytest.raises(WorktreeSetupError, match="exit 1") as error:
        python.ensure_python_dependencies(tmp_path)
    assert "secret-token" not in str(error.value)
    assert (tmp_path / ".meow/python-install.pending").exists()
    assert not (tmp_path / ".meow/python-install.fingerprint").exists()
    monkeypatch.setattr(python, "run_captured", successful)
    python.ensure_python_dependencies(tmp_path)
    assert not (tmp_path / ".meow/python-install.pending").exists()


@pytest.mark.parametrize("decision", ["deny", "ask"])
def test_permissions_preflight_every_command(tmp_path, installer, decision):
    project(tmp_path)
    with pytest.raises(WorktreeSetupError, match="policy"):
        python.ensure_python_dependencies(
            tmp_path,
            command_decision=lambda argv: decision if "pip" in argv else None,
        )
    assert installer == []


def test_unrecognized_project_has_no_side_effects(tmp_path, installer):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length=88\n")
    python.ensure_python_dependencies(tmp_path)
    assert installer == []
    assert not (tmp_path / ".meow").exists()


def test_only_task_worktrees_are_prepared(tmp_path, monkeypatch, installer):
    project(tmp_path)
    prepare_environment(tmp_path, tmp_path / ".", {}, new=True)
    assert installer == []
    active = tmp_path / "task"
    project(active)
    prepare_environment(tmp_path, active, {}, new=True)
    assert installer
    first = installer[-1][0][0]
    other = tmp_path / "other"
    project(other)
    prepare_environment(tmp_path, other, {}, new=False)
    assert installer[-1][0][0] != first


def test_linked_worktree_selected_in_place_is_prepared(
    tmp_path, monkeypatch, installer
):
    from meow.infrastructure import worktree

    project(tmp_path)
    (tmp_path / ".git").write_text("gitdir: linked metadata")
    monkeypatch.setattr(worktree, "_is_linked_worktree", lambda _root: True)
    monkeypatch.setattr(
        subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
    )
    prepare_environment(tmp_path, tmp_path, {}, new=False)
    assert installer


def test_environment_is_local_and_preserves_overrides(tmp_path, installer):
    project(tmp_path)
    python.ensure_python_dependencies(tmp_path)
    original = dict(os.environ)
    env = python.python_environment(tmp_path, {"EXAMPLE": "yes"})
    assert env["PATH"].split(os.pathsep)[0] == str(python.venv_python(tmp_path).parent)
    assert env["VIRTUAL_ENV"] == str(tmp_path / ".venv")
    assert env["EXAMPLE"] == "yes"
    assert dict(os.environ) == original
    assert (
        python.python_environment(tmp_path, {"PATH": "explicit"})["PATH"] == "explicit"
    )


def test_check_routes_prepared_environment(tmp_path, monkeypatch, installer):
    from meow.infrastructure import checks

    project(tmp_path)
    python.ensure_python_dependencies(tmp_path)
    captured = AsyncMock(return_value=(0, "", False))
    monkeypatch.setattr(checks, "run_captured_async", captured)
    checks.run_check(
        tmp_path, checks.Check("lint", "lint", "python"), revision="r", fingerprint="f"
    )
    assert captured.call_args.args[2]["VIRTUAL_ENV"] == str(tmp_path / ".venv")


def test_lint_uses_root_venv_for_component_cwd(tmp_path, monkeypatch, installer):
    from meow.infrastructure import lint
    from meow.project.config_models import LintCommand

    project(tmp_path)
    python.ensure_python_dependencies(tmp_path)
    (tmp_path / "component").mkdir()
    captured = AsyncMock(return_value=(0, "", False))
    monkeypatch.setattr(lint, "run_captured_async", captured)
    asyncio.run(
        lint.check_lint_commands(
            tmp_path, [LintCommand(command="python", cwd=python.Path("component"))], 10
        )
    )
    assert captured.call_args.args[2]["VIRTUAL_ENV"] == str(tmp_path / ".venv")


def test_environment_excludes_are_recorded_in_git_metadata(
    tmp_path, monkeypatch, installer
):
    project(tmp_path)
    (tmp_path / ".git").write_text("gitdir: unused")
    exclude = tmp_path / "git metadata/info/exclude"
    exclude.parent.mkdir(parents=True)
    exclude.write_text("# keep existing\n")
    git = Mock(
        side_effect=[
            subprocess.CompletedProcess([], 1, "", ""),
            subprocess.CompletedProcess([], 0, str(exclude) + "\n", ""),
        ]
    )
    monkeypatch.setattr(subprocess, "run", git)
    python.ensure_python_dependencies(tmp_path)
    assert exclude.read_text() == "# keep existing\n/.venv/\n"
    assert not (tmp_path / ".gitignore").exists()
    python.ensure_python_dependencies(tmp_path)
    assert git.call_count == len(("check-ignore", "rev-parse"))


def test_launch_resolves_against_child_path(tmp_path, monkeypatch, installer):
    from meow.infrastructure import test_runner

    project(tmp_path)
    python.ensure_python_dependencies(tmp_path)
    env = python.python_environment(tmp_path)
    resolver = Mock(return_value=str(python.venv_python(tmp_path)))
    monkeypatch.setattr(test_runner.shutil, "which", resolver)
    assert test_runner._launch_argv("python", (), env)[0] == str(
        python.venv_python(tmp_path)
    )
    resolver.assert_called_once_with("python", path=env["PATH"])


@pytest.mark.parametrize("result", [(None, "secret", True), (1, "secret", False)])
def test_diagnostics_are_safe_and_timeout_is_forwarded(
    tmp_path, monkeypatch, installer, result
):
    project(tmp_path)
    run = Mock(return_value=result)
    monkeypatch.setattr(python, "run_captured", run)
    actions = []
    timeout = 42
    with pytest.raises(WorktreeSetupError):
        python.ensure_python_dependencies(
            tmp_path, timeout=timeout, record_action=actions.append
        )
    assert run.call_args.args[3] == timeout
    assert actions[0]["status"] == "failed"
    assert "secret" not in repr(actions)


def test_malformed_manifest_is_actionable(tmp_path, installer):
    (tmp_path / "pyproject.toml").write_text("[bad")
    with pytest.raises(WorktreeSetupError, match="manifest"):
        python.ensure_python_dependencies(tmp_path)
    assert installer == []


def test_sdk_agent_environment_routes_without_leaking_parent_env(
    tmp_path, monkeypatch, installer
):
    from meow.agents.base import Agent, ProjectContext

    project(tmp_path)
    python.ensure_python_dependencies(tmp_path)
    monkeypatch.setenv("TASK3_PARENT_ONLY", "private")
    original = dict(os.environ)
    agent = Agent(ProjectContext(tmp_path, {"models": {"tester": "model"}, "lint": []}))
    options = agent.options(system_prompt="role", allowed_tools=["Bash"], role="tester")
    assert options.env["VIRTUAL_ENV"] == str(tmp_path / ".venv")
    assert options.env["PATH"].split(os.pathsep)[0] == str(
        python.venv_python(tmp_path).parent
    )
    assert "TASK3_PARENT_ONLY" not in options.env
    assert dict(os.environ) == original


def test_test_commands_receive_venv_environment(tmp_path, monkeypatch, installer):
    from meow.infrastructure import test_runner
    from meow.project.config_models import VerificationCommand

    project(tmp_path)
    python.ensure_python_dependencies(tmp_path)
    process = Mock(returncode=0)
    process.communicate = AsyncMock(return_value=(b"", b""))
    launch = AsyncMock(return_value=process)
    monkeypatch.setattr(test_runner.asyncio, "create_subprocess_exec", launch)
    asyncio.run(
        test_runner._run_test(
            tmp_path, VerificationCommand(command="python", cwd=python.Path("."))
        )
    )
    assert launch.call_args.kwargs["env"]["VIRTUAL_ENV"] == str(tmp_path / ".venv")


def test_shared_environment_link_is_refused(tmp_path, installer):
    project(tmp_path)
    outside = tmp_path / "shared"
    outside.mkdir()
    try:
        (tmp_path / ".venv").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(WorktreeSetupError, match="symlink"):
        python.ensure_python_dependencies(tmp_path)
    assert installer == []


def test_fallback_installs_declared_dev_group_without_new_pip_flags(
    tmp_path, installer
):
    (tmp_path / "pyproject.toml").write_text(
        '[dependency-groups]\ndev=["pytest", {include-group="lint"}]\nlint=["ruff"]\n'
    )
    python.ensure_python_dependencies(tmp_path)
    assert installer[-1][0][-2:] == ["pytest", "ruff"]


@pytest.mark.parametrize(
    "manifest",
    [
        'project="invalid"',
        '[dependency-groups]\ndev=[{include-group="dev"}]',
    ],
)
def test_invalid_manifest_structure_is_actionable(tmp_path, installer, manifest):
    (tmp_path / "pyproject.toml").write_text(manifest)
    with pytest.raises(WorktreeSetupError, match=r"manifest|dependency group"):
        python.ensure_python_dependencies(tmp_path)
    assert installer == []


def test_per_file_lint_fix_and_recheck_use_worktree_env(
    tmp_path, monkeypatch, installer
):
    from meow.infrastructure import lint
    from meow.project.config_models import LintCommand

    project(tmp_path)
    python.ensure_python_dependencies(tmp_path)
    (tmp_path / "component").mkdir()
    captured = AsyncMock(return_value=(0, "", False))
    monkeypatch.setattr(lint, "run_captured_async", captured)
    command = LintCommand(
        command="ruff", fix_flag="--fix", cwd=python.Path("component")
    )
    result = asyncio.run(
        lint._run_one_lint_command(tmp_path, command, "example.py", 10)
    )
    assert result is None
    assert len(captured.call_args_list) == len(("fix", "recheck"))
    for call in captured.call_args_list:
        assert call.args[2]["VIRTUAL_ENV"] == str(tmp_path / ".venv")
        assert call.args[2]["PATH"].split(os.pathsep)[0] == str(
            python.venv_python(tmp_path).parent
        )


def test_cli_sprint_prepares_separate_python_worktree(tmp_path, installer):
    from tests.execution.test_auto_onboarding import _run

    launch = tmp_path / "launch"
    launch.mkdir()
    task = tmp_path / "task"
    project(task)
    _run(task, record_root=launch)
    assert installer


def test_parallel_verification_uses_worktree_env(tmp_path, monkeypatch, installer):
    from meow.tasks import runner
    from meow.tasks.model import TaskSpec

    project(tmp_path)
    python.ensure_python_dependencies(tmp_path)
    launch = AsyncMock(return_value=(0, "", False))
    monkeypatch.setattr(runner, "run_captured_async", launch)
    spec = TaskSpec("task", (), ("src",), ("python check.py",))
    asyncio.run(runner.verify_task(spec, tmp_path))
    assert launch.call_args.args[2]["VIRTUAL_ENV"] == str(tmp_path / ".venv")


def test_pip_destination_is_forced_but_registry_environment_is_preserved(
    tmp_path, monkeypatch, installer
):
    project(tmp_path)
    config = tmp_path / "pip.ini"
    config.write_text(
        "[global]\nindex-url=https://user:password@example.invalid/simple\n"
        "target=outside\nprefix=outside\nuser=true\n"
    )
    for key in ("PIP_TARGET", "PIP_PREFIX", "PIP_ROOT", "PIP_PYTHON"):
        monkeypatch.setenv(key, str(tmp_path / "shared"))
    monkeypatch.setenv("PIP_USER", "1")
    monkeypatch.setenv("PIP_CONFIG_FILE", str(config))
    monkeypatch.setenv("PIP_INDEX_URL", "https://user:secret@example.invalid/simple")
    python.ensure_python_dependencies(tmp_path)
    argv, _cwd, env, _timeout = installer[-1]
    assert (
        not {"PIP_TARGET", "PIP_PREFIX", "PIP_ROOT", "PIP_PYTHON", "PIP_USER"}
        & env.keys()
    )
    assert argv[argv.index("--prefix") + 1] == str(tmp_path / ".venv")
    assert "--no-user" in argv
    assert env["PIP_CONFIG_FILE"] == os.devnull
    assert env["PIP_INDEX_URL"] == os.environ["PIP_INDEX_URL"]


def test_final_lint_launch_does_not_restore_pythonhome(
    tmp_path, monkeypatch, installer
):
    from meow.infrastructure import lint
    from meow.project.config_models import LintCommand

    project(tmp_path)
    python.ensure_python_dependencies(tmp_path)
    monkeypatch.setenv("PYTHONHOME", str(tmp_path / "wrong python"))
    launch = AsyncMock(return_value=(0, "", False))
    monkeypatch.setattr(lint, "run_captured_async", launch)
    asyncio.run(lint._run_one_lint_command(tmp_path, LintCommand("ruff"), "x.py", 10))
    assert not launch.call_args.args[2].get("PYTHONHOME")


def test_final_sdk_launch_neutralizes_inherited_pythonhome(
    tmp_path, monkeypatch, installer
):
    from claude_agent_sdk._internal.transport import subprocess_cli

    from meow.agents.base import Agent, ProjectContext

    project(tmp_path)
    python.ensure_python_dependencies(tmp_path)
    monkeypatch.setenv("PYTHONHOME", str(tmp_path / "wrong python"))
    monkeypatch.setenv("CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK", "1")
    launch = AsyncMock(side_effect=OSError("mock launch"))
    monkeypatch.setattr(subprocess_cli.anyio, "open_process", launch)
    agent = Agent(ProjectContext(tmp_path, {"models": {"tester": "model"}, "lint": []}))
    options = agent.options(
        system_prompt="role",
        allowed_tools=["Bash"],
        role="tester",
        cli_path=sys.executable,
    )
    transport = subprocess_cli.SubprocessCLITransport(prompt="hello", options=options)
    with pytest.raises(Exception, match="mock launch"):
        asyncio.run(transport.connect())
    assert not launch.call_args.kwargs["env"].get("PYTHONHOME")


@pytest.mark.parametrize("option", ["-r", "-c", "--requirement=", "--constraint="])
def test_nested_requirement_changes_refresh_install(tmp_path, installer, option):
    (tmp_path / "deps").mkdir()
    separator = "" if option.endswith("=") else " "
    (tmp_path / "requirements.txt").write_text(f"{option}{separator}deps/base.txt\n")
    included = tmp_path / "deps/base.txt"
    included.write_text("pytest==8\n")
    python.ensure_python_dependencies(tmp_path)
    count = len(installer)
    python.ensure_python_dependencies(tmp_path)
    assert len(installer) == count
    included.write_text("pytest==9\n")
    python.ensure_python_dependencies(tmp_path)
    assert len(installer) > count


@pytest.mark.parametrize(
    "include",
    ["https://example.invalid/deps.txt", "../outside.txt", "${UNKNOWN}/deps.txt"],
)
def test_untrackable_includes_never_reuse_evidence(tmp_path, installer, include):
    (tmp_path / "requirements.txt").write_text(f"-r {include}\n")
    python.ensure_python_dependencies(tmp_path)
    count = len(installer)
    python.ensure_python_dependencies(tmp_path)
    assert len(installer) > count


def test_multiple_includes_and_cycles_are_bounded(tmp_path, installer):
    (tmp_path / "requirements.txt").write_text("-r base.txt -c constraints.txt\n")
    (tmp_path / "base.txt").write_text("-r requirements.txt\n")
    constraint = tmp_path / "constraints.txt"
    constraint.write_text("pytest==8\n")
    python.ensure_python_dependencies(tmp_path)
    count = len(installer)
    python.ensure_python_dependencies(tmp_path)
    assert len(installer) == count
    constraint.write_text("pytest==9\n")
    python.ensure_python_dependencies(tmp_path)
    assert len(installer) > count


def test_requirement_file_limit_prevents_reuse(tmp_path, monkeypatch, installer):
    (tmp_path / "requirements.txt").write_text("-r base.txt\n")
    (tmp_path / "base.txt").write_text("pytest\n")
    monkeypatch.setattr(python, "_MAX_REQUIREMENT_FILES", 1)
    python.ensure_python_dependencies(tmp_path)
    count = len(installer)
    python.ensure_python_dependencies(tmp_path)
    assert len(installer) > count


def test_parallel_worker_prepares_python_environment(tmp_path, monkeypatch, installer):
    from meow.execution.run_state import RunStore
    from meow.tasks import runner
    from meow.tasks.model import TaskGraph, TaskSpec

    task = tmp_path / "task"
    project(task)
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt", request="work", repo=tmp_path, worktree=tmp_path, branch="dev"
    )

    async def execute(*_args, **kwargs):
        await kwargs["prepare"](task)
        return SimpleNamespace(passed=True, revision="a" * 40)

    monkeypatch.setattr(runner, "execute_in_worktrees", execute)
    sprint = SimpleNamespace(
        config={}, repo_dir=tmp_path, active_working_dir=lambda: tmp_path
    )
    graph = TaskGraph((TaskSpec("task", (), ("src",), ()),))
    assert asyncio.run(
        runner.run_parallel_plan(sprint, graph, tmp_path / "plan.md", store, record.id)
    )
    assert installer
