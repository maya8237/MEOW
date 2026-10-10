import json
import os

import pytest

from meow import installer
from meow.installer import _runtime
from meow.project.onboarding import OnboardingReport


def test_append_plugin_dir_preserves_existing_env_entries_and_settings(tmp_path):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    first = tmp_path / "one"
    second = tmp_path / "two"
    settings.write_text(
        json.dumps(
            {
                "permissions": {"allow": ["Bash(git status)"]},
                "env": {
                    "CLAUDE_CODE_PLUGIN_DIRS": f"{first}{os.pathsep}{second}"
                },
            }
        ),
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
        ask=lambda question: (prompts.append(question) or next(answers)),
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

    monkeypatch.setattr(_runtime, "append_plugin_dir", lambda *_args, **_kwargs: None)

    def fake_onboard(root):
        calls.append(root)
        return OnboardingReport((".gitignore",), None, (), None)

    monkeypatch.setattr(_runtime, "onboard_project", fake_onboard)
    answers = iter((str(project), ""))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    assert installer.main(["--repo-dir", str(repo)]) == 0

    output = capsys.readouterr().out
    assert calls == [project]
    assert "/meow:onboard" in output
    assert 'meow run "Add CSV export" --name csv-export --work-dir <project>' in output
    assert "/meow:run Add CSV export" in output
    assert "(no further menu)" not in output
