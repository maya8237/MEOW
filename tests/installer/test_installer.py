import json
import os
import subprocess

import pytest

from meow import installer
from meow.installer import _runtime
from meow.project import repos as repos_module
from meow.project.onboarding import OnboardingReport


@pytest.fixture(autouse=True)
def _private_repo_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(repos_module, "registry_path", lambda: tmp_path / "repos.toml")
    monkeypatch.setattr(
        "meow.project.config.user_config_path", lambda: tmp_path / "user.toml"
    )


def _registered_project(tmp_path, name):
    project = tmp_path / name
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    assert _runtime.onboard_project(project).error is None
    repos_module.remember(project)
    return project


def test_setup_refreshes_registered_repo_and_reports_available_features(tmp_path):
    project = _registered_project(tmp_path, "existing")
    local = project / ".meow" / "config.local.toml"
    local.unlink()
    shared = project / ".meow" / "config.toml"
    shared.write_text("# customized\nmax_rounds = 3\n", encoding="utf-8")
    original = shared.read_bytes()
    output, done = [], []
    answers = iter(("y", str(project), str(project / "."), ""))

    _runtime._onboard_projects(lambda _: next(answers), output.append, onboarded=done)

    assert local.is_file()
    assert shared.read_bytes() == original
    assert done == [project]
    assert sum(f"{project}: onboarded" in line for line in output) == 1
    assert any("tester dev server" in line for line in output)
    assert any("claude /meow:onboard" in line for line in output)


def test_refresh_is_noop_and_preserves_private_config(tmp_path):
    project = _registered_project(tmp_path, "existing")
    local = project / ".meow" / "config.local.toml"
    local.write_text(
        '[github.mcp]\ncommand = "npx"\n[github.mcp.env]\nTOKEN = "private-token"\n',
        encoding="utf-8",
    )
    paths = [project / ".gitignore", project / ".meow" / "config.toml", local]
    original = [path.read_bytes() for path in paths]
    for _ in range(2):
        answers = iter(("y", ""))
        output, done = [], []
        _runtime._onboard_projects(
            lambda _, answers=answers: next(answers), output.append, onboarded=done
        )
        assert done == [project]
        assert any("no files changed" in line for line in output)
        assert any("Already configured: github" in line for line in output)
        assert "private-token" not in "\n".join(output)
        assert [path.read_bytes() for path in paths] == original


def test_refresh_continues_after_invalid_registered_repo(tmp_path):
    broken = tmp_path / "not-git"
    broken.mkdir()
    repos_module.remember(broken)
    project = _registered_project(tmp_path, "healthy")
    missing = project / ".meow" / "config.local.toml"
    missing.unlink()
    answers = iter(("y", ""))
    output, done = [], []

    _runtime._onboard_projects(lambda _: next(answers), output.append, onboarded=done)

    assert missing.is_file()
    assert done == [project]
    assert any(str(broken) in line and "skipped" in line for line in output)


def test_declining_setup_does_not_refresh_registered_projects(tmp_path):
    project = _registered_project(tmp_path, "existing")
    local = project / ".meow" / "config.local.toml"
    local.unlink()

    _runtime._onboard_projects(lambda _: "n", lambda _: None)

    assert not local.exists()


@pytest.mark.parametrize("manifest", ["[]", "null", "42"])
def test_refresh_continues_with_non_object_package_manifest(tmp_path, manifest):
    malformed = _registered_project(tmp_path, "malformed")
    (malformed / "package.json").write_text(manifest, encoding="utf-8")
    healthy = _registered_project(tmp_path, "healthy")
    missing = healthy / ".meow" / "config.local.toml"
    missing.unlink()
    answers = iter(("y", ""))
    done = []

    _runtime._onboard_projects(lambda _: next(answers), lambda _: None, onboarded=done)

    assert done == [malformed, healthy]
    assert missing.is_file()


def test_append_plugin_dir_preserves_existing_env_entries_and_settings(tmp_path):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    first = tmp_path / "one"
    second = tmp_path / "two"
    settings.write_text(
        json.dumps({
            "permissions": {"allow": ["Bash(git status)"]},
            "env": {"CLAUDE_CODE_PLUGIN_DIRS": f"{first}{os.pathsep}{second}"},
        }),
        encoding="utf-8",
    )

    installer.append_plugin_dir(settings, tmp_path / "meow", separator=os.pathsep)

    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["permissions"]["allow"] == ["Bash(git status)"]
    assert data["env"]["CLAUDE_CODE_PLUGIN_DIRS"].split(os.pathsep) == [
        str(first),
        str(second),
        str((tmp_path / "meow").resolve()),
    ]


def test_append_plugin_dir_is_idempotent_and_creates_missing_settings(tmp_path):
    settings = tmp_path / ".claude" / "settings.json"
    plugin_dir = tmp_path / "meow"

    installer.append_plugin_dir(settings, plugin_dir, separator=";")
    installer.append_plugin_dir(settings, plugin_dir, separator=";")

    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["env"]["CLAUDE_CODE_PLUGIN_DIRS"] == str(plugin_dir.resolve())


def test_append_plugin_dir_does_not_overwrite_malformed_settings(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="valid JSON"):
        installer.append_plugin_dir(settings, tmp_path / "meow")

    assert settings.read_text(encoding="utf-8") == "{not json"


def test_star_expands_only_immediate_child_directories(tmp_path):
    (tmp_path / "one").mkdir()
    (tmp_path / "one" / "nested").mkdir()
    (tmp_path / "two").mkdir()
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")

    matches = installer.expand_project_pattern(str(tmp_path / "*"))

    assert matches == (tmp_path / "one", tmp_path / "two")


def test_double_star_explains_star_and_requires_recursive_confirmation(tmp_path):
    (tmp_path / "one" / "nested").mkdir(parents=True)
    answers = iter(("no", "yes"))
    messages = []
    prompts = []

    matches = installer.expand_project_pattern(
        str(tmp_path / "**"),
        ask=lambda question: prompts.append(question) or next(answers),
        output=messages.append,
    )

    assert tmp_path / "one" in matches
    assert tmp_path / "one" / "nested" in matches
    assert tmp_path not in matches
    assert any("Did you mean `*`" in prompt for prompt in prompts)


def test_double_star_can_be_downgraded_to_first_level(tmp_path):
    (tmp_path / "one" / "nested").mkdir(parents=True)

    matches = installer.expand_project_pattern(
        str(tmp_path / "**"), ask=lambda _: "yes"
    )

    assert matches == (tmp_path / "one",)


def test_double_star_declined_confirmation_skips_pattern(tmp_path):
    (tmp_path / "one" / "nested").mkdir(parents=True)
    answers = iter(("no", "no"))

    matches = installer.expand_project_pattern(
        str(tmp_path / "**"), ask=lambda _: next(answers)
    )

    assert matches == ()


def test_downgraded_double_star_still_requires_a_first_level_pattern(tmp_path):
    (tmp_path / "one" / "project").mkdir(parents=True)
    messages = []

    matches = installer.expand_project_pattern(
        str(tmp_path / "**" / "project"),
        ask=lambda _: "yes",
        output=messages.append,
    )

    assert matches == ()
    assert any("immediate child" in message for message in messages)


def test_literal_project_path_is_returned(tmp_path):
    project = tmp_path / "project"
    project.mkdir()

    assert installer.expand_project_pattern(str(project)) == (project,)


def test_main_onboards_each_literal_destination_and_prints_next_steps(
    tmp_path, monkeypatch, capsys
):
    repo = tmp_path / "meow"
    repo.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    calls = []

    monkeypatch.setattr(
        _runtime, "register_checkout_plugin", lambda *_args, **_kwargs: None
    )

    def fake_onboard(root):
        calls.append(root)
        return OnboardingReport((".gitignore",), None, (), None)

    monkeypatch.setattr(_runtime, "onboard_project", fake_onboard)
    answers = iter(("n", "y", str(project), ""))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    assert installer.main(["--repo-dir", str(repo), "--setup"]) == 0

    output = capsys.readouterr().out
    assert calls == [project]
    assert "Add projects one at a time" in output
    assert "Optional features not enabled" not in output
    assert "Get started" not in output  # shown after the scripts' "Done!"


def test_main_registers_new_and_already_onboarded_projects(tmp_path, monkeypatch):
    repo = tmp_path / "meow"
    repo.mkdir()
    onboarded = tmp_path / "already"
    onboarded.mkdir()
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    registry = tmp_path / "home" / ".meow" / "repos.toml"
    monkeypatch.setattr(repos_module, "registry_path", lambda: registry)
    monkeypatch.setattr(
        _runtime, "register_checkout_plugin", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        _runtime,
        "onboard_project",
        lambda root: OnboardingReport((), None, (), None),
    )
    answers = iter(("n", "y", str(onboarded), str(fresh), ""))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    assert installer.main(["--repo-dir", str(repo), "--setup"]) == 0

    saved = repos_module._saved()
    assert str(onboarded.resolve()) in saved
    assert str(fresh.resolve()) in saved


def test_next_steps_flag_prints_only_the_closing_guidance(capsys):
    assert installer.main(["--next-steps"]) == 0

    output = capsys.readouterr().out
    assert "Get started:" in output
    assert "claude /meow:onboard" in output
    assert 'meow run "Add CSV export" --name csv-export --work-dir <project>' in output
    assert "claude /meow:run Add CSV export" in output
    assert "(no further menu)" not in output


def test_main_skips_project_setup_when_the_user_declines(tmp_path, monkeypatch):
    repo = tmp_path / "meow"
    repo.mkdir()
    calls = []
    prompts = []

    monkeypatch.setattr(
        _runtime, "register_checkout_plugin", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(_runtime, "onboard_project", lambda root: calls.append(root))

    def fake_input(prompt):
        prompts.append(prompt)
        return "n"

    monkeypatch.setattr("builtins.input", fake_input)

    assert installer.main(["--repo-dir", str(repo), "--setup"]) == 0
    assert calls == []
    assert len(prompts) == len(("bun", "projects"))
    assert "[Y/n]" in prompts[0]


def test_main_exits_quietly_on_ctrl_c(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "meow"
    repo.mkdir()
    monkeypatch.setattr(
        _runtime, "register_checkout_plugin", lambda *_args, **_kwargs: None
    )

    def interrupt(_prompt):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupt)

    assert (
        installer.main(["--repo-dir", str(repo), "--setup"])
        == _runtime.EXIT_INTERRUPTED
    )
    assert "Setup stopped." in capsys.readouterr().out


def test_cancelled_message_says_how_many_projects_were_set_up():
    assert "no projects were set up" in _runtime._cancelled_message(0)
    assert "1 project set up before you stopped" in _runtime._cancelled_message(1)
    two = _runtime._cancelled_message(2)
    assert "2 projects set up before you stopped" in two
    assert "claude /meow:onboard" in two


def test_register_checkout_plugin_enables_marketplace_and_keeps_env(tmp_path):
    import json

    from meow.project.claude_settings import register_checkout_plugin

    repo = tmp_path / "meow"
    (repo / ".claude-plugin").mkdir(parents=True)
    (repo / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({"name": "meow", "plugins": []}), encoding="utf-8"
    )
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps({"env": {"KEEP": "1"}}),
        encoding="utf-8",
    )

    register_checkout_plugin(settings, repo)
    register_checkout_plugin(settings, repo)

    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["extraKnownMarketplaces"]["meow"]["source"] == {
        "source": "directory",
        "path": str(repo.resolve()),
    }
    assert data["enabledPlugins"]["meow@meow"] is True
    assert data["env"]["KEEP"] == "1"
