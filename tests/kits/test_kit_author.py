import pytest

from meow.cli import cli as cli_module
from meow.cli.kits_cli import kits_command
from meow.project.custom import kit_author

from .conftest import SKILL

MIN_PROBLEMS = 3
AGENT = 'description = "{d}"\nprompt = "{d}"\n'
HOOK = 'description = "g"\nevent = "PreToolUse"\nmatcher = "Write"\ncommand = "{c}"\n'


def run(*argv):
    parsed = cli_module._build_arg_parser().parse_args(["kits", *argv])
    kits_command(parsed, parsed.working_dir and __import__("pathlib").Path("."))


def test_scaffold_is_a_valid_kit(tmp_path):
    created = kit_author.scaffold(tmp_path / "demo-kit")
    assert "meow-kit.toml" in created and "skills/hello/SKILL.md" in created
    report = kit_author.validate_directory(tmp_path / "demo-kit")
    assert report.ok and not report.warnings
    assert report.summary == ["1 skills, 1 agents"]
    assert (
        "meow kits add owner/demo-kit" in (tmp_path / "demo-kit/README.md").read_text()
    )


def test_scaffold_refuses_a_used_folder_and_bad_names(tmp_path):
    (tmp_path / "used").mkdir()
    (tmp_path / "used" / "file").write_text("x")
    with pytest.raises(ValueError, match="not empty"):
        kit_author.scaffold(tmp_path / "used")
    with pytest.raises(ValueError, match="invalid kit name"):
        kit_author.scaffold(tmp_path / "ok", name="Bad Name")
    assert not (tmp_path / "ok").exists()


def test_every_problem_in_a_kit_is_reported_at_once(tmp_path):
    kit_author.scaffold(tmp_path / "k")
    root = tmp_path / "k"
    (root / "skills/bad").mkdir()
    (root / "skills/bad/SKILL.md").write_text(SKILL.format(name="wrong", body="x"))
    (root / "agents/broken.toml").write_text('description = "x"\n')
    (root / "hooks").mkdir()
    (root / "hooks/h.toml").write_text(
        'description = "h"\nevent = "Nope"\ncommand = "x"\n'
    )
    (root / "skills/hello/link").symlink_to("/etc/hostname")
    (root / "NOTES.txt").write_text("hi")
    (root / "skills/empty").mkdir()
    report = kit_author.validate_directory(root)
    text = "\n".join(report.problems)
    assert "must match its directory" in text
    assert "must set 'prompt'" in text
    assert "symbolic link" in text
    assert len(report.problems) > MIN_PROBLEMS
    assert any("NOTES.txt" in w for w in report.warnings)
    assert any("skills/empty: no SKILL.md" in w for w in report.warnings)


def test_bad_manifest_and_empty_directory(tmp_path):
    (tmp_path / "e").mkdir()
    assert "no skills" in kit_author.validate_directory(tmp_path / "e").problems[0]
    (tmp_path / "e" / "meow-kit.toml").write_text("[config.permissions]\nallow = []\n")
    (tmp_path / "e" / "agents").mkdir()
    (tmp_path / "e" / "agents/a.toml").write_text(AGENT.format(d="a"))
    assert (
        "kits may set only" in kit_author.validate_directory(tmp_path / "e").problems[0]
    )
    assert kit_author.validate_directory(tmp_path / "nope").problems == [
        f"{tmp_path / 'nope'}: not a directory"
    ]


def test_hooks_are_listed_so_authors_see_what_they_ship(tmp_path):
    kit_author.scaffold(tmp_path / "k")
    (tmp_path / "k/hooks").mkdir()
    (tmp_path / "k/hooks/guard.toml").write_text(HOOK.format(c="python guard.py"))
    report = kit_author.validate_directory(tmp_path / "k")
    assert report.ok
    assert "hook guard (PreToolUse) will run: python guard.py" in report.summary


def test_unknown_workflow_agent_is_caught(tmp_path):
    kit_author.scaffold(tmp_path / "k")
    (tmp_path / "k/workflows").mkdir()
    (tmp_path / "k/workflows/w.toml").write_text(
        'description = "w"\n[[steps]]\nid = "s"\nagent = "does-not-exist"\n'
    )
    report = kit_author.validate_directory(tmp_path / "k")
    assert not report.ok and "does-not-exist" in "\n".join(report.problems)


def test_cli_init_then_validate_then_fail_with_exit_1(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run("init", "mine")
    run("validate", "mine")
    assert "Kit is valid." in capsys.readouterr().out
    (tmp_path / "mine/agents/explainer.toml").write_text('description = "x"\n')
    with pytest.raises(SystemExit) as stopped:
        run("validate", "mine")
    assert stopped.value.code == 1
    assert "error: " in capsys.readouterr().out


def test_cli_validates_a_git_url_before_you_publish_it(
    make_kit, tmp_path, capsys, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    repo = make_kit({"skills/s/SKILL.md": SKILL.format(name="s", body="x")})
    run("validate", f"file://{repo.path}")
    out = capsys.readouterr().out
    assert "Checking" in out and "Kit is valid." in out
