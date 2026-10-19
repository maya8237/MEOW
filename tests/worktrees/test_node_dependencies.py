import json
import os
import subprocess

import pytest

from meow.infrastructure import node_dependencies as node
from meow.infrastructure.worktree_setup import (
    WorktreeSetupError,
    run_setup,
    validate_setup,
)


@pytest.mark.parametrize(
    "error",
    [
        "error: lockfile had changes, but lockfile is frozen",
        "error: Outdated lockfile: --frozen-lockfile was specified",
    ],
)
def test_bun_frozen_error_retries_unfrozen(tmp_path, monkeypatch, error):
    calls, actions = [], []
    monkeypatch.setattr(node.shutil, "which", lambda name: name)

    def install(argv, *_args):
        calls.append(argv)
        return (1, error, False) if len(calls) == 1 else (0, "", False)

    monkeypatch.setattr(node, "run_captured", install)
    node.run_node_install(
        tmp_path,
        ["bun", "install", "--force", "--frozen-lockfile"],
        record_action=actions.append,
    )
    assert calls == [
        ["bun", "install", "--force", "--frozen-lockfile"],
        ["bun", "install", "--force"],
    ]
    assert [action["status"] for action in actions] == ["failed", "passed"]
    assert not (tmp_path / ".meow/node-install.pending").exists()


@pytest.mark.parametrize(
    ("manager", "output", "timed_out"),
    [
        ("bun", "registry unavailable", False),
        ("bun", "lockfile is frozen", True),
        ("pnpm", "lockfile is frozen", False),
    ],
)
def test_other_install_errors_do_not_retry(  # ruff: ignore[too-many-arguments]
    tmp_path, monkeypatch, *, manager, output, timed_out
):
    calls = []
    monkeypatch.setattr(node.shutil, "which", lambda name: name)

    def install(argv, *_args):
        calls.append(argv)
        return 1, output, timed_out

    monkeypatch.setattr(node, "run_captured", install)
    with pytest.raises(WorktreeSetupError):
        node.run_node_install(tmp_path, [manager, "install", "--frozen-lockfile"])
    assert len(calls) == 1


def test_bun_unfrozen_retry_obeys_policy(tmp_path, monkeypatch):
    monkeypatch.setattr(node.shutil, "which", lambda name: name)
    monkeypatch.setattr(
        node, "run_captured", lambda *_args: (1, "lockfile is frozen", False)
    )
    decisions = []

    def decision(argv):
        decisions.append(argv)
        return "allow" if "--frozen-lockfile" in argv else "deny"

    with pytest.raises(WorktreeSetupError, match="denied"):
        node.run_node_install(
            tmp_path,
            ["bun", "install", "--frozen-lockfile"],
            command_decision=decision,
        )
    assert decisions == [
        ["bun", "install", "--frozen-lockfile"],
        ["bun", "install"],
    ]


def test_failed_bun_retry_stops_and_keeps_pending_marker(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(node.shutil, "which", lambda name: name)

    def install(argv, *_args):
        calls.append(argv)
        return 1, "error: lockfile is frozen", False

    monkeypatch.setattr(node, "run_captured", install)
    with pytest.raises(WorktreeSetupError, match="lockfile is frozen"):
        node.run_node_install(tmp_path, ["bun", "install", "--frozen-lockfile"])
    assert calls == [["bun", "install", "--frozen-lockfile"], ["bun", "install"]]
    assert (tmp_path / ".meow/node-install.pending").exists()


@pytest.mark.parametrize(
    ("manager", "lock", "argv"),
    [
        ("bun@1.4.0", "bun.lock", ["bun", "install", "--frozen-lockfile"]),
        ("npm@11.0.0", "package-lock.json", ["npm", "ci"]),
        ("pnpm@10.0.0", "pnpm-lock.yaml", ["pnpm", "install", "--frozen-lockfile"]),
        ("yarn@1.22.22", "yarn.lock", ["yarn", "install", "--frozen-lockfile"]),
        ("yarn@4.0.0", "yarn.lock", ["yarn", "install", "--immutable"]),
    ],
)
def test_missing_modules_use_project_manager(  # ruff: ignore[too-many-arguments]
    tmp_path, monkeypatch, *, manager, lock, argv
):
    (tmp_path / "package.json").write_text(
        json.dumps({
            "packageManager": manager,
            "dependencies": {"example": "1.0.0"},
        })
    )
    (tmp_path / lock).touch()
    calls = []
    monkeypatch.setattr(node.shutil, "which", lambda name: name)

    def install(command, cwd, _env, _timeout):
        calls.append((command, cwd))
        (cwd / "node_modules").mkdir()
        (cwd / "node_modules" / "example").mkdir()
        return 0, "", False

    monkeypatch.setattr(node, "run_captured", install)
    run_setup(tmp_path, tmp_path, validate_setup(None))
    node.ensure_node_dependencies(tmp_path)
    assert calls == [(argv, tmp_path)]


def test_nested_projects_and_workspaces(tmp_path, monkeypatch):
    app = tmp_path / "frontend"
    package = app / "packages" / "lib"
    package.mkdir(parents=True)
    (app / "package.json").write_text(
        '{"workspaces":["packages/*"],"dependencies":{"x":"1"}}'
    )
    (package / "package.json").write_text('{"dependencies":{"y":"1"}}')
    calls = []
    monkeypatch.setattr(node.shutil, "which", lambda name: name)

    def install(argv, cwd, _env, _timeout):
        calls.append((argv, cwd))
        (cwd / "node_modules").mkdir()
        return 0, "", False

    monkeypatch.setattr(node, "run_captured", install)
    node.ensure_node_dependencies(tmp_path)
    assert calls == [(["npm", "install"], app)]


def test_policy_blocks_automatic_install_and_missing_tool_is_actionable(
    tmp_path, monkeypatch
):
    (tmp_path / "package.json").write_text('{"dependencies":{"x":"1"}}')
    with pytest.raises(WorktreeSetupError, match="denied"):
        node.ensure_node_dependencies(tmp_path, command_decision=lambda _argv: "deny")
    monkeypatch.setattr(node.shutil, "which", lambda _name: None)
    with pytest.raises(WorktreeSetupError, match=r"npm.*PATH"):
        node.ensure_node_dependencies(tmp_path)


def test_install_failure_includes_diagnostics(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text('{"dependencies":{"x":"1"}}')
    monkeypatch.setattr(node.shutil, "which", lambda name: name)
    monkeypatch.setattr(
        node, "run_captured", lambda *_args: (1, "registry unavailable", False)
    )
    with pytest.raises(WorktreeSetupError, match="registry unavailable"):
        node.ensure_node_dependencies(tmp_path)


def test_broken_transitive_bun_store_link_triggers_repair(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text(
        '{"packageManager":"bun@1.4.0","dependencies":{"direct":"1"}}'
    )
    modules = tmp_path / "node_modules"
    (modules / "direct").mkdir(parents=True)
    store = modules / ".bun"
    store.mkdir()
    target = tmp_path / "cache" / "transitive"
    target.mkdir(parents=True)
    link = store / "transitive@1"
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=True,
            capture_output=True,
        )
    else:
        link.symlink_to(target, target_is_directory=True)
    target.rmdir()

    def repair(directory, argv, **_kwargs):
        assert directory == tmp_path
        assert argv == ["bun", "install"]
        target.mkdir()

    monkeypatch.setattr(node, "run_node_install", repair)
    node.ensure_node_dependencies(tmp_path)
    assert link.is_dir()


def test_empty_or_failed_partial_install_is_repaired(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text('{"dependencies":{"x":"1"}}')
    modules = tmp_path / "node_modules"
    modules.mkdir()
    monkeypatch.setattr(node.shutil, "which", lambda name: name)
    attempts = []

    def install(_argv, cwd, _env, _timeout):
        attempts.append(cwd)
        (modules / "partial").touch()
        if len(attempts) > 1:
            (modules / "x").mkdir()
        return (1, "interrupted", False) if len(attempts) == 1 else (0, "", False)

    monkeypatch.setattr(node, "run_captured", install)
    with pytest.raises(WorktreeSetupError, match="interrupted"):
        node.ensure_node_dependencies(tmp_path)
    node.ensure_node_dependencies(tmp_path)
    node.ensure_node_dependencies(tmp_path)
    assert len(attempts) == len(("failed", "passed"))


def test_tracked_yarn_pnp_loader_does_not_hide_missing_dependencies(
    tmp_path, monkeypatch
):
    (tmp_path / "package.json").write_text(
        '{"packageManager":"yarn@4.0.0","dependencies":{"x":"1"}}'
    )
    (tmp_path / "yarn.lock").touch()
    (tmp_path / ".pnp.cjs").touch()
    calls = []
    monkeypatch.setattr(node.shutil, "which", lambda name: name)
    monkeypatch.setattr(
        node, "run_captured", lambda argv, *_args: (calls.append(argv) or 0, "", False)
    )
    node.ensure_node_dependencies(tmp_path)
    assert calls == [["yarn", "install", "--immutable"]]


def test_workspace_member_dependencies_are_repaired_when_root_store_exists(
    tmp_path, monkeypatch
):
    app = tmp_path / "packages" / "app"
    app.mkdir(parents=True)
    (tmp_path / "package.json").write_text(
        '{"packageManager":"pnpm@10.0.0","workspaces":["packages/*"]}'
    )
    (tmp_path / "pnpm-lock.yaml").touch()
    (tmp_path / "pnpm-workspace.yaml").write_text('packages:\n  - "packages/*"\n')
    (tmp_path / "node_modules" / ".pnpm").mkdir(parents=True)
    (app / "package.json").write_text('{"dependencies":{"x":"1"}}')
    calls = []
    monkeypatch.setattr(node.shutil, "which", lambda name: name)

    def install(argv, cwd, _env, _timeout):
        calls.append((argv, cwd))
        (app / "node_modules" / "x").mkdir(parents=True)
        return 0, "", False

    monkeypatch.setattr(node, "run_captured", install)
    node.ensure_node_dependencies(tmp_path)
    node.ensure_node_dependencies(tmp_path)
    assert calls == [(["pnpm", "install", "--frozen-lockfile"], tmp_path)]
