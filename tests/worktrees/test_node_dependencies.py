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


@pytest.fixture(autouse=True)
def _home(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


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
        (
            "bun@1.4.0",
            "bun.lock",
            ["bun", "install", "--linker", "isolated", "--frozen-lockfile"],
        ),
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


def test_successful_but_incomplete_bun_install_is_repaired_once(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text(
        '{"packageManager":"bun@1.4.0","dependencies":{"x":"1"}}'
    )
    (tmp_path / "bun.lock").touch()
    monkeypatch.setattr(node.shutil, "which", lambda name: name)
    calls = []

    def install(argv, cwd, _env, _timeout):
        calls.append(argv)
        (cwd / "node_modules").mkdir(exist_ok=True)
        if "--force" in argv:
            (cwd / "node_modules/x").mkdir()
        return 0, "", False

    monkeypatch.setattr(node, "run_captured", install)
    node.ensure_node_dependencies(tmp_path)
    assert (tmp_path / "node_modules/x").is_dir()
    node.ensure_node_dependencies(tmp_path)
    assert calls == [
        ["bun", "install", "--linker", "isolated", "--frozen-lockfile"],
        ["bun", "install", "--linker", "isolated", "--frozen-lockfile", "--force"],
    ]


def test_bun_install_fails_if_force_still_leaves_missing_modules(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text(
        '{"packageManager":"bun@1.4.0","dependencies":{"x":"1"}}'
    )
    monkeypatch.setattr(node.shutil, "which", lambda name: name)
    calls = []
    monkeypatch.setattr(
        node, "run_captured", lambda argv, *_args: (calls.append(argv) or 0, "", False)
    )
    with pytest.raises(WorktreeSetupError, match="still incomplete"):
        node.ensure_node_dependencies(tmp_path)
    isolated = ["bun", "install", "--linker", "isolated"]
    assert calls == [isolated, [*isolated, "--force"]]


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
        assert argv == ["bun", "install", "--linker", "isolated"]
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


def _bun_project(tmp_path, dev=True):
    (tmp_path / "package.json").write_text(
        json.dumps({
            "packageManager": "bun@1.4.0",
            "dependencies": {"a": "1"},
            "devDependencies": {"b": "1"} if dev else {},
        })
    )


def test_project_hoisted_choice_is_respected(tmp_path):
    _bun_project(tmp_path)
    (tmp_path / "bunfig.toml").write_text('[install]\nlinker = "hoisted"\n')
    data = json.loads((tmp_path / "package.json").read_text())
    assert node.install_command(tmp_path, data) == ["bun", "install"]


def test_user_hoisted_setting_cannot_disable_shared_store(tmp_path, _home):
    _bun_project(tmp_path)
    (_home / ".bunfig.toml").write_text('[install]\nlinker = "hoisted"\n')
    data = json.loads((tmp_path / "package.json").read_text())
    assert node.install_command(tmp_path, data)[2:] == ["--linker", "isolated"]


def test_malformed_bunfig_is_ignored(tmp_path):
    _bun_project(tmp_path)
    (tmp_path / "bunfig.toml").write_text("[install\nlinker = ")
    data = json.loads((tmp_path / "package.json").read_text())
    assert "isolated" in node.install_command(tmp_path, data)


@pytest.mark.parametrize("where", ["project", "user"])
def test_production_bunfig_does_not_require_dev_dependencies(
    tmp_path, monkeypatch, _home, where
):
    _bun_project(tmp_path)
    target = tmp_path if where == "project" else _home
    (target / ("bunfig.toml" if where == "project" else ".bunfig.toml")).write_text(
        "[install]\nproduction = true\n"
    )
    calls = []
    monkeypatch.setattr(node.shutil, "which", lambda name: name)

    def install(argv, cwd, _env, _timeout):
        calls.append(argv)
        (cwd / "node_modules" / "a").mkdir(parents=True)
        return 0, "", False

    monkeypatch.setattr(node, "run_captured", install)
    node.ensure_node_dependencies(tmp_path)
    node.ensure_node_dependencies(tmp_path)
    assert len(calls) == 1  # no forced reinstall, and ready afterwards


def test_still_incomplete_error_names_missing_and_forbids_hoisting(
    tmp_path, monkeypatch
):
    _bun_project(tmp_path)
    monkeypatch.setattr(node.shutil, "which", lambda name: name)

    def install(_argv, cwd, _env, _timeout):
        (cwd / "node_modules" / "a").mkdir(parents=True, exist_ok=True)
        return 0, "", False

    monkeypatch.setattr(node, "run_captured", install)
    with pytest.raises(WorktreeSetupError, match=r"missing: b\).*do not switch"):
        node.ensure_node_dependencies(tmp_path)


def test_bun_without_linker_support_retries_without_it(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(node.shutil, "which", lambda name: name)

    def install(argv, *_args):
        calls.append(argv)
        if "--linker" in argv:
            return 1, "error: Unknown option '--linker'", False
        return 0, "", False

    monkeypatch.setattr(node, "run_captured", install)
    node.run_node_install(tmp_path, ["bun", "install", "--linker", "isolated"])
    assert calls == [["bun", "install", "--linker", "isolated"], ["bun", "install"]]


def test_install_timeout_is_configurable_and_reported(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(node.shutil, "which", lambda name: name)
    monkeypatch.setenv("MEOW_NODE_INSTALL_TIMEOUT", "1234")
    monkeypatch.setattr(
        node, "run_captured", lambda *args: (seen.append(args[3]) or None, "", True)
    )
    with pytest.raises(WorktreeSetupError, match="after 1234s"):
        node.run_node_install(tmp_path, ["bun", "install"])
    assert seen == [1234.0]
    monkeypatch.setenv("MEOW_NODE_INSTALL_TIMEOUT", "junk")
    assert node._install_timeout() == pytest.approx(node._DEFAULT_TIMEOUT)


def test_unreadable_modules_directory_means_not_ready(tmp_path, monkeypatch):
    _bun_project(tmp_path)
    (tmp_path / "node_modules").mkdir()
    monkeypatch.setattr(
        node.Path, "iterdir", lambda _self: (_ for _ in ()).throw(PermissionError())
    )
    assert node._missing(tmp_path, {"dependencies": {"a": "1"}}) == ["node_modules"]


def test_production_bunfig_warns_that_dev_dependencies_are_skipped(
    tmp_path, monkeypatch
):
    (tmp_path / "bunfig.toml").write_text("[install]\nproduction = true\n")
    warnings = []
    monkeypatch.setattr(
        node._LOGGER, "warning", lambda event, **_kw: warnings.append(event)
    )
    node._warn_bun_config(tmp_path)
    assert warnings == ["bun_production_skips_dev_dependencies"]


def test_configured_install_timeout_reaches_the_install(tmp_path, monkeypatch):
    _bun_project(tmp_path, dev=False)
    seen = []
    monkeypatch.setattr(node.shutil, "which", lambda name: name)

    def install(_argv, cwd, _env, timeout):
        seen.append(timeout)
        (cwd / "node_modules" / "a").mkdir(parents=True)
        return 0, "", False

    monkeypatch.setattr(node, "run_captured", install)
    monkeypatch.setenv("MEOW_NODE_INSTALL_TIMEOUT", "50")
    node.ensure_node_dependencies(tmp_path, timeout=2400)
    assert seen == [2400.0]


@pytest.mark.parametrize("bad", [0, -5, "10", True])
def test_install_timeout_config_must_be_positive_seconds(bad):
    with pytest.raises(ValueError, match="install_timeout"):
        validate_setup({"install_timeout": bad})


def test_install_timeout_config_is_parsed_without_changing_the_manifest():
    from meow.infrastructure.worktree_setup import install_timeout

    raw = {"install_timeout": 1800}
    assert validate_setup(raw) == {"copy": [], "commands": []}
    assert install_timeout(raw) == pytest.approx(1800.0)
    assert install_timeout(None) is None


EXPECTED_INSTALLS = 2


def _bun_install_fake(monkeypatch, envs=None):
    calls = []
    monkeypatch.setattr(node.shutil, "which", lambda name: name)

    def install(argv, cwd, env, _timeout):
        calls.append(argv)
        if envs is not None:
            envs.append(env)
        for name in ("a", "b"):
            (cwd / "node_modules" / name).mkdir(parents=True, exist_ok=True)
        (cwd / "node_modules" / ".bun").mkdir(exist_ok=True)
        return 0, "", False

    monkeypatch.setattr(node, "run_captured", install)
    return calls


def test_bun_install_enables_the_shared_store(tmp_path, monkeypatch):
    _bun_project(tmp_path)
    monkeypatch.delenv(node._STORE_ENV, raising=False)
    envs = []
    _bun_install_fake(monkeypatch, envs)
    node.ensure_node_dependencies(tmp_path)
    assert envs[0][node._STORE_ENV] == "1"


@pytest.mark.parametrize(
    "setting", ["globalStore = false", 'linker = "hoisted"'], ids=["store", "hoisted"]
)
def test_project_opt_out_keeps_shared_store_off_and_says_so(
    tmp_path, monkeypatch, capsys, setting
):
    _bun_project(tmp_path)
    monkeypatch.delenv(node._STORE_ENV, raising=False)
    (tmp_path / "bunfig.toml").write_text(f"[install]\n{setting}\n")
    envs = []
    _bun_install_fake(monkeypatch, envs)
    node.ensure_node_dependencies(tmp_path)
    assert node._STORE_ENV not in envs[0]
    assert "skips the shared store" in capsys.readouterr().err


def test_caller_store_setting_is_not_overridden(tmp_path, monkeypatch):
    _bun_project(tmp_path)
    monkeypatch.setenv(node._STORE_ENV, "0")
    envs = []
    _bun_install_fake(monkeypatch, envs)
    node.ensure_node_dependencies(tmp_path)
    assert envs[0][node._STORE_ENV] == "0"


def test_other_managers_do_not_get_the_bun_store_variable(tmp_path):
    assert node._STORE_ENV not in node._install_env(tmp_path, ["pnpm", "install"])


def test_changed_lockfile_triggers_reinstall_and_unchanged_does_not(
    tmp_path, monkeypatch
):
    _bun_project(tmp_path)
    (tmp_path / "bun.lock").write_text("v1")
    calls = _bun_install_fake(monkeypatch)
    node.ensure_node_dependencies(tmp_path)
    node.ensure_node_dependencies(tmp_path)
    assert len(calls) == 1
    (tmp_path / "bun.lock").write_text("v2")
    node.ensure_node_dependencies(tmp_path)
    node.ensure_node_dependencies(tmp_path)
    assert len(calls) == EXPECTED_INSTALLS


def test_changed_declared_dependencies_trigger_reinstall(tmp_path, monkeypatch):
    _bun_project(tmp_path)
    calls = _bun_install_fake(monkeypatch)
    node.ensure_node_dependencies(tmp_path)
    _bun_project(tmp_path, dev=False)
    node.ensure_node_dependencies(tmp_path)
    assert len(calls) == EXPECTED_INSTALLS


def test_healthy_install_without_a_record_is_kept_and_baselined(tmp_path, monkeypatch):
    _bun_project(tmp_path)
    (tmp_path / "bun.lock").write_text("v1")
    for name in ("a", "b"):
        (tmp_path / "node_modules" / name).mkdir(parents=True)
    (tmp_path / "node_modules" / ".bun").mkdir()
    calls = _bun_install_fake(monkeypatch)
    node.ensure_node_dependencies(tmp_path)
    assert calls == []
    assert (tmp_path / node._FINGERPRINT).is_file()
    (tmp_path / "bun.lock").write_text("v2")
    node.ensure_node_dependencies(tmp_path)
    assert len(calls) == 1


def test_hoisted_bun_layout_gets_a_conversion_hint(tmp_path, monkeypatch, capsys):
    _bun_project(tmp_path)
    for name in ("a", "b"):
        (tmp_path / "node_modules" / name).mkdir(parents=True)
    calls = _bun_install_fake(monkeypatch)
    node.ensure_node_dependencies(tmp_path)
    assert calls == []  # hinted, never converted
    assert "bun install --force --linker isolated" in capsys.readouterr().err
