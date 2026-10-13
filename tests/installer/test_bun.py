import tomllib

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
    from meow.infrastructure import node_dependencies
    from meow.project.onboarding import OnboardingReport

    project = tmp_path / "project"
    project.mkdir()
    (project / "package.json").write_text('{"packageManager":"bun@1.4.0"}')
    monkeypatch.setattr(_runtime, "register_checkout_plugin", lambda *_args: None)
    monkeypatch.setattr(
        _runtime, "onboard_project", lambda _root: OnboardingReport((), None, ())
    )
    monkeypatch.setattr(node_dependencies.shutil, "which", lambda name: name)
    calls = []

    def install(argv, cwd, _env, _timeout):
        calls.append((argv, cwd))
        return 0, "", False

    monkeypatch.setattr(node_dependencies, "run_captured", install)
    answers = iter(("", "y", str(project), ""))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    assert _runtime.main(["--repo-dir", str(tmp_path)]) == 0
    assert calls == [(["bun", "install", "--force", "--linker", "isolated"], project)]
    assert (project / "bunfig.toml").is_file()


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
    from meow.infrastructure import node_dependencies
    from meow.project.repos import remember

    project = tmp_path / "registered"
    project.mkdir()
    (project / "package.json").write_text('{"packageManager":"bun@1.4.0"}')
    remember(project)
    monkeypatch.setattr(_runtime, "register_checkout_plugin", lambda *_args: None)
    monkeypatch.setattr(node_dependencies.shutil, "which", lambda name: name)
    monkeypatch.setattr(
        node_dependencies, "run_captured", lambda *_args: (0, "", False)
    )
    answers = iter(("", "n"))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    assert _runtime.main(["--repo-dir", str(tmp_path)]) == 0
    assert (project / "bunfig.toml").is_file()


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
    assert tomllib.loads((tmp_path / "bunfig.toml").read_text())["install"] == {
        "linker": "isolated",
        "globalStore": True,
    }
