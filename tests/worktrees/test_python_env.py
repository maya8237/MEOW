"""Venv-aware setup timeouts, repair and retry."""

import pytest

from meow.execution.run_failures import classify_failure, recognised_transient
from meow.infrastructure import worktree_setup
from meow.infrastructure.python_env import (
    env_command_env,
    is_env_command,
    repair_venvs,
    venv_broken,
)
from meow.infrastructure.worktree_setup import (
    WorktreeSetupError,
    run_setup,
    validate_setup,
)


@pytest.mark.parametrize(
    "argv",
    [
        ["python", "-m", "venv", ".venv"],
        ["py", "-m", "venv", ".venv"],
        ["uv", "sync"],
        ["uv", "venv"],
        ["uv", "pip", "install", "-e", "."],
        ["pip", "install", "-r", "r.txt"],
        ["python", "-m", "pip", "install", "x"],
        ["py", "-m", "pip", "install", "x"],
        ["poetry", "install"],
        ["pdm", "install"],
        ["hatch", "env", "create"],
        ["virtualenv", ".venv"],
    ],
)
def test_env_commands_are_recognised(argv):
    assert is_env_command(argv)


@pytest.mark.parametrize("argv", [[], ["npm", "install"], ["uv", "run", "x"]])
def test_other_commands_are_not_env_commands(argv):
    assert not is_env_command(argv)


def test_env_command_env_defaults_do_not_override():
    env = env_command_env(["uv", "sync"], {"PIP_RETRIES": "9", "PATH": "/bin"})
    assert env["PIP_RETRIES"] == "9"
    assert env["PIP_DEFAULT_TIMEOUT"] == "60"
    assert env["UV_HTTP_TIMEOUT"] == "120"
    assert env["UV_HTTP_RETRIES"] == "5"
    assert env["POETRY_REQUESTS_TIMEOUT"] == "60"
    assert "UV_HTTP_TIMEOUT" not in env_command_env(["make"], {})


def _stub(monkeypatch, outcomes):
    calls = []

    def fake(argv, cwd, env, timeout, **_kw):
        calls.append((argv, env, timeout))
        return outcomes[len(calls) - 1]

    monkeypatch.setattr(worktree_setup, "run_captured", fake)
    return calls


def _setup(tmp_path, *argv):
    run_setup(tmp_path, tmp_path, validate_setup({"commands": [list(argv)]}))


def test_env_setup_uses_install_timeout(tmp_path, monkeypatch):
    calls = _stub(monkeypatch, [(0, "", False)])
    _setup(tmp_path, "uv", "sync")
    assert [calls[0][2]] == [900]
    assert calls[0][1]["UV_HTTP_TIMEOUT"] == "120"


def test_other_setup_commands_keep_the_short_timeout(tmp_path, monkeypatch):
    calls = _stub(monkeypatch, [(0, "", False)])
    _setup(tmp_path, "make", "prep")
    assert [calls[0][2]] == [300]


def test_env_setup_timeout_retries_with_double(tmp_path, monkeypatch):
    calls = _stub(monkeypatch, [(None, "", True), (0, "", False)])
    _setup(tmp_path, "uv", "sync")
    assert [call[2] for call in calls] == [900, 1800]


def test_broken_venv_is_recreated_on_retry(tmp_path, monkeypatch):
    broken = tmp_path / ".venv"
    broken.mkdir()
    (broken / "stub").write_text("x", encoding="utf-8")
    seen = []

    def fake(argv, cwd, env, timeout, **_kw):
        seen.append(broken.exists())
        return (None, "", True) if len(seen) == 1 else (0, "", False)

    monkeypatch.setattr(worktree_setup, "run_captured", fake)
    _setup(tmp_path, "uv", "sync")
    assert seen == [True, False]


def test_second_setup_timeout_parks_run(tmp_path, monkeypatch):
    _stub(monkeypatch, [(None, "", True), (None, "", True)])
    with pytest.raises(WorktreeSetupError) as caught:
        _setup(tmp_path, "uv", "sync")
    assert caught.value.cause == "setup_timeout"
    assert classify_failure(caught.value) == ("transient", "setup_timeout")
    assert recognised_transient(caught.value)
    assert (tmp_path / ".meow" / "worktree-setup.pending").exists()


def test_venv_without_pyvenv_cfg_is_broken_and_healthy_one_is_kept(tmp_path):
    bad = tmp_path / "venv"
    bad.mkdir()
    assert venv_broken(bad)
    good = tmp_path / ".venv"
    (good / "bin").mkdir(parents=True)
    (good / "pyvenv.cfg").write_text("home = x", encoding="utf-8")
    (good / "bin" / "python").symlink_to(__import__("sys").executable)
    assert not venv_broken(good)
    assert repair_venvs(tmp_path) == [bad]
    assert good.exists() and not bad.exists()


def test_repair_keeps_tracked_dirs_and_reports_only_removed(tmp_path):
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    tracked = tmp_path / "venv"
    tracked.mkdir()
    (tracked / "data.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "venv"], cwd=tmp_path, check=True)
    broken = tmp_path / ".venv"
    broken.mkdir()
    assert repair_venvs(tmp_path) == [broken]
    assert tracked.exists()


def test_slow_probe_does_not_delete_a_venv(tmp_path, monkeypatch):
    import subprocess

    from meow.infrastructure import python_env

    (tmp_path / "pyvenv.cfg").write_text("home = x", encoding="utf-8")

    def slow(*_a, **_k):
        raise subprocess.TimeoutExpired("python", 60)

    monkeypatch.setattr(python_env.subprocess, "run", slow)
    assert not venv_broken(tmp_path)


def test_non_env_timeout_does_not_repair_venvs(tmp_path, monkeypatch):
    broken = tmp_path / ".venv"
    broken.mkdir()
    _stub(monkeypatch, [(None, "", True), (0, "", False)])
    _setup(tmp_path, "make", "prep")
    assert broken.exists()
