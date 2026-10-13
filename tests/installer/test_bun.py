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
