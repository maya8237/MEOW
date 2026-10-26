import os
import sys
import time
import tomllib
from pathlib import Path

import pytest

from meow.installer import _runtime


def test_bun_option_defaults_to_yes_and_preserves_user_config(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(_runtime.Path, "home", lambda: home)
    config = home / ".bunfig.toml"
    original = (
        '# keep this\n[install]\nregistry = "https://example.test"\n'
        'linker = "hoisted"\n[run]\nshell = "system"\n'
    )
    config.write_text(original, encoding="utf-8")
    output = []

    assert _runtime._setup_bun(lambda _prompt: "", output.append)
    parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    assert parsed["install"] == {
        "registry": "https://example.test",
        "linker": "isolated",
        "globalStore": True,
    }
    assert parsed["run"] == {"shell": "system"}
    assert "# keep this" in config.read_text(encoding="utf-8")
    assert any("bun install --force --linker isolated" in line for line in output)


def test_installer_accepting_bun_migrates_entered_project(tmp_path, monkeypatch):
    from meow.project.onboarding import OnboardingReport

    project = tmp_path / "project"
    project.mkdir()
    (project / "package.json").write_text('{"packageManager":"bun@1.4.0"}')
    monkeypatch.setattr(_runtime, "register_checkout_plugin", lambda *_args: None)
    monkeypatch.setattr(
        _runtime, "onboard_project", lambda _root: OnboardingReport((), None, ())
    )
    calls = []
    monkeypatch.setattr(
        _runtime,
        "start_bun_migration",
        lambda projects, _output: calls.extend(projects),
    )
    answers = iter(("", "y", str(project), ""))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    assert _runtime.main(["--repo-dir", str(tmp_path)]) == 0
    assert calls == [project]


@pytest.mark.parametrize(
    "original",
    [
        '[install.cache]\ndir = "cache"\n',
        'install.linker = "hoisted"\ninstall.registry = "https://example.test"\n',
        '["install"] # keep\n"linker" = "hoisted"\n\n'
        "[install.cache]\ndisable = false\n",
    ],
)
def test_bunfig_preserves_alternative_table_layouts(tmp_path, original):
    from meow.installer.bun import configure_bunfig

    path = tmp_path / "bunfig.toml"
    path.write_text(original)
    configure_bunfig(path)
    parsed = tomllib.loads(path.read_text())
    assert parsed["install"]["linker"] == "isolated"
    assert parsed["install"]["globalStore"] is True


def test_declining_bun_does_not_write_user_config(tmp_path, monkeypatch):
    monkeypatch.setattr(_runtime.Path, "home", lambda: tmp_path)
    assert not _runtime._setup_bun(lambda _prompt: "n", lambda _line: None)
    assert not (tmp_path / ".bunfig.toml").exists()


def test_accepting_bun_migrates_registered_repos_without_new_onboarding(
    tmp_path, monkeypatch
):
    from meow.project.repos import remember

    project = tmp_path / "registered"
    project.mkdir()
    (project / "package.json").write_text('{"packageManager":"bun@1.4.0"}')
    remember(project)
    monkeypatch.setattr(_runtime, "register_checkout_plugin", lambda *_args: None)
    calls = []
    monkeypatch.setattr(
        _runtime, "start_bun_migration", lambda projects, _out: calls.extend(projects)
    )
    answers = iter(("", "n"))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    assert _runtime.main(["--repo-dir", str(tmp_path)]) == 0
    assert calls == [project]


def test_bun_config_is_idempotent_and_invalid_toml_is_preserved(tmp_path):
    from meow.installer.bun import configure_bunfig

    config = tmp_path / "bunfig.toml"
    configure_bunfig(config)
    first = config.read_bytes()
    configure_bunfig(config)
    assert config.read_bytes() == first
    config.write_text("[install\n", encoding="utf-8")
    with pytest.raises(ValueError):
        configure_bunfig(config)
    assert config.read_text(encoding="utf-8") == "[install\n"


def test_migrates_onboarded_bun_repo_and_rebuilds_existing_modules(
    tmp_path, monkeypatch
):
    from meow.infrastructure import node_dependencies
    from meow.installer.bun import migrate_bun_projects

    (tmp_path / "package.json").write_text(
        '{"packageManager":"bun@1.4.0","dependencies":{"example":"1.0.0"}}',
        encoding="utf-8",
    )
    (tmp_path / "bun.lock").touch()
    (tmp_path / "node_modules").mkdir()
    calls = []
    monkeypatch.setattr(node_dependencies.shutil, "which", lambda name: name)

    def install(argv, cwd, _env, _timeout):
        calls.append((argv, cwd))
        return 0, "", False

    monkeypatch.setattr(node_dependencies, "run_captured", install)
    migrate_bun_projects([tmp_path], lambda _line: None)
    assert calls == [
        (
            ["bun", "install", "--force", "--linker", "isolated", "--frozen-lockfile"],
            tmp_path,
        )
    ]
    assert not (tmp_path / "bunfig.toml").exists()


def test_migration_warns_without_changing_project_bunfig(tmp_path, monkeypatch):
    from meow.installer import bun

    (tmp_path / "package.json").write_text('{"packageManager":"bun@1.4.0"}')
    config = tmp_path / "bunfig.toml"
    original = "[install]\nglobalStore = false\n"
    config.write_text(original)
    monkeypatch.setattr(bun, "run_node_install", lambda *_args: None)
    output = []
    bun.migrate_bun_projects([tmp_path], output.append)
    assert config.read_text() == original
    assert any("Warning" in line and "override" in line for line in output)


def test_migration_discovers_all_bun_folders_before_installing(  # ruff: ignore[too-many-statements]
    tmp_path, monkeypatch
):
    from meow.infrastructure.worktree_setup import WorktreeSetupError
    from meow.installer import bun

    first = tmp_path / "first"
    nested = first / "packages" / "app"
    second = tmp_path / "second"
    for directory in (first, nested, second):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "package.json").write_text(
            '{"packageManager":"bun@1.4.0","workspaces":["packages/*"]}'
        )
    discovered, installed = [], []
    real_projects = bun.node_projects

    def discover(*args, **kwargs):
        for directory, data in real_projects(*args, **kwargs):
            discovered.append(directory)
            yield directory, data

    def install(directory, _argv):
        assert discovered == [first, nested, second]
        installed.append(directory)
        if directory == first:
            raise WorktreeSetupError("registry unavailable")

    monkeypatch.setattr(bun, "node_projects", discover)
    monkeypatch.setattr(bun, "run_node_install", install)
    output = []
    bun.migrate_bun_projects([first, second, first], output.append)
    assert installed == [first, nested, second]
    assert any("migration failed" in line for line in output)


@pytest.mark.parametrize(
    "manifest",
    [
        "{",
        '{"packageManager":"unsupported@1"}',
        '{"workspaces":42}',
        '{"workspaces":[""]}',
    ],
)
def test_bun_discovery_continues_after_invalid_sibling(tmp_path, manifest):
    from meow.installer.bun import _bun_projects

    broken, valid = tmp_path / "a-broken", tmp_path / "b-bun"
    for directory, data in (
        (broken, manifest),
        (valid, '{"packageManager":"bun@1.4.0"}'),
    ):
        directory.mkdir()
        (directory / "package.json").write_text(data)
    output = []
    assert _bun_projects([tmp_path], output.append) == {valid: tmp_path}
    assert any(str(broken) in line and "failed" in line for line in output)


def test_bun_migration_runs_in_background_and_logs_nested_app(tmp_path, monkeypatch):  # ruff: ignore[too-many-statements]
    from meow.installer.bun import start_bun_migration

    app = tmp_path / "repo" / "frontend"
    app.mkdir(parents=True)
    (app / "package.json").write_text('{"packageManager":"bun@1.4.0"}')
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    started, release = tmp_path / "started", tmp_path / "release"
    fake = bin_dir / "fake.py"
    fake.write_text(
        "import time\nfrom pathlib import Path\n"
        f"Path({str(started)!r}).touch()\n"
        "for _ in range(100):\n"
        f"    if Path({str(release)!r}).exists(): break\n    time.sleep(.1)\n"
    )
    executable = bin_dir / ("bun.cmd" if os.name == "nt" else "bun")
    executable.write_text(
        f'@echo off\n"{sys.executable}" "{fake}"\n'
        if os.name == "nt"
        else f'#!/bin/sh\nexec "{sys.executable}" "{fake}"\n'
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    output = []
    job = start_bun_migration([app.parent], output.append)
    log = Path(job["log"])
    try:
        for _ in range(100):
            if started.exists():
                break
            time.sleep(0.1)
        assert started.exists(), log.read_text(encoding="utf-8")
        assert not release.exists()  # installer returned while Bun is still waiting
        assert any(str(log) in line for line in output)
    finally:
        release.touch()
    for _ in range(100):
        if "Bun migration finished" in log.read_text(encoding="utf-8"):
            break
        time.sleep(0.1)
    assert "Bun migration finished" in log.read_text(encoding="utf-8")
    assert "frontend" in log.read_text(encoding="utf-8")
    assert not (app / "bunfig.toml").exists()


def test_update_offer_skips_when_store_is_already_configured(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bunfig.toml").write_text(
        '[install]\nlinker = "isolated"\nglobalStore = true\n'
    )
    monkeypatch.setattr(_runtime.Path, "home", lambda: home)

    def unexpected(*_args):
        raise AssertionError("must not prompt or migrate again")

    monkeypatch.setattr("builtins.input", unexpected)
    monkeypatch.setattr(_runtime, "start_bun_migration", unexpected)
    assert _runtime.main(["--update-bun"]) == 0


def test_update_offer_configures_and_migrates_registered_projects(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    home.mkdir()
    project, nested = tmp_path / "app", tmp_path / "app" / "web"
    monkeypatch.setattr(_runtime.Path, "home", lambda: home)
    monkeypatch.setattr(_runtime, "_saved", lambda: [str(project), str(nested)])
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    calls = []
    monkeypatch.setattr(
        _runtime, "start_bun_migration", lambda projects, _out: calls.extend(projects)
    )
    assert _runtime.main(["--update-bun"]) == 0
    assert calls == [project]  # nested repo rides with its parent
    assert "globalStore = true" in (home / ".bunfig.toml").read_text()


def test_update_offer_declined_changes_nothing(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(_runtime.Path, "home", lambda: home)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    monkeypatch.setattr(
        _runtime, "start_bun_migration", lambda *_args: pytest.fail("migrated")
    )
    assert _runtime.main(["--update-bun"]) == 0
    assert not (home / ".bunfig.toml").exists()


def test_meow_update_runs_the_bun_offer_only_interactively(monkeypatch):
    from meow.installer import maintenance

    calls = []
    monkeypatch.setattr(maintenance, "_local_install", lambda: False)
    monkeypatch.setattr(
        maintenance.subprocess, "run", lambda argv, **_kw: calls.append(argv)
    )
    monkeypatch.setattr(maintenance.sys.stdin, "isatty", lambda: False)
    maintenance.offer_bun_store(Path("/release"))
    assert calls == []
    monkeypatch.setattr(maintenance.sys.stdin, "isatty", lambda: True)
    maintenance.offer_bun_store(Path("/release"))
    assert calls[0][-3:] == ["-m", "meow.installer", "--update-bun"]
