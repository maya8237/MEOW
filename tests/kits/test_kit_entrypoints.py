import json
import runpy
from pathlib import Path

import pytest

from meow.cli import cli
from meow.cli.kits_cli import kits_command
from meow.installer import _runtime
from meow.project.custom import kit_ops

from .conftest import SKILL

UNINSTALL = Path(__file__).resolve().parents[2] / "scripts" / "uninstall.py"


@pytest.fixture
def repo(make_kit):
    return make_kit({"skills/s/SKILL.md": SKILL.format(name="s", body="x")})


def run(project, *argv):
    kits_command(cli._build_arg_parser().parse_args(["kits", *argv]), project)


def test_cli_add_list_update_uninstall(repo, project, user_dir, capsys):
    run(project, "add", str(repo.path), "--yes", "--name", "demo")
    run(project, "list")
    out = capsys.readouterr().out
    assert "Added kit 'demo'" in out and "demo" in out
    assert (Path.home() / ".claude/skills/s/SKILL.md").is_file()  # published
    run(project, "update", "--check")
    run(project, "uninstall", "demo")
    assert not (Path.home() / ".claude/skills/s").exists()  # and withdrawn
    assert "No kits installed" in "\n".join(kit_ops.listing(project, user_dir))


def test_cli_rejects_unknown_part_and_missing_kit(repo, project, user_dir):
    with pytest.raises(ValueError, match="--only accepts"):
        run(project, "add", str(repo.path), "--yes", "--only", "bogus")
    with pytest.raises(ValueError, match="no kit named"):
        run(project, "uninstall", "ghost")


def test_non_terminal_add_needs_yes(repo, project, user_dir):
    with pytest.raises(ValueError, match="pass --yes"):
        run(project, "add", str(repo.path))


def test_add_does_not_collide_with_update_option(repo, project, user_dir):
    run(project, "add", str(repo.path), "--yes", "--update", "pinned")
    assert kit_ops.collect_specs(project, user_dir)[0].update == "pinned"


def test_installer_flag_is_repeatable_and_adds_user_kits(repo, make_kit, user_dir):
    other = make_kit({"agents/a.toml": 'description = "a"\nprompt = "a"\n'})
    args = _runtime._parser().parse_args([
        "--repo-dir", ".", "--kit", str(repo.path), "--kit", str(other.path),
    ])  # fmt: skip
    assert args.kit == [str(repo.path), str(other.path)]
    assert _runtime._optional_setup(args) == 0
    names = [s.scope for s in kit_ops.collect_specs(Path.cwd(), user_dir)]
    assert names == ["user", "user"]


def test_installer_reports_a_bad_kit_and_keeps_going(repo, user_dir, capsys):
    args = _runtime._parser().parse_args([
        "--repo-dir", ".", "--kit", "/nonexistent/kit", "--kit", str(repo.path),
    ])  # fmt: skip
    assert _runtime._optional_setup(args) == 1
    assert len(kit_ops.collect_specs(Path.cwd(), user_dir)) == 1
    assert "skipped" in capsys.readouterr().out


def test_installer_prompt_adds_then_stops_on_blank(repo, user_dir):
    answers = iter([str(repo.path), "y", ""])
    _runtime._offer_kits(lambda _prompt: next(answers), lambda _line: None)
    assert len(kit_ops.collect_specs(Path.cwd(), user_dir)) == 1


def test_uninstaller_prompts_per_kit_and_keeps_the_rest(
    repo, make_kit, user_dir, monkeypatch
):
    other = make_kit({"agents/a.toml": 'description = "a"\nprompt = "a"\n'})
    for kit, name in ((repo, "one"), (other, "two")):
        kit_ops.install(Path.cwd(), user_dir, str(kit.path), name=name, output=str)
    (user_dir / "config.toml").write_text(
        "# mine\n" + (user_dir / "config.toml").read_text(), encoding="utf-8"
    )
    module = runpy.run_path(str(UNINSTALL))
    assert module["user_kit_names"](Path.home()) == ["one", "two"]
    answers = iter(["y", "n"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    chosen = module["ask_kits"](Path.home())
    assert chosen == ["one"]
    module["remove_kit"](Path.home(), "one")
    assert module["user_kit_names"](Path.home()) == ["two"]
    assert "# mine" in (user_dir / "config.toml").read_text()
    assert list(json.loads((user_dir / "kits.lock").read_text())["kits"]) == ["two"]
    assert len(list((user_dir / "kits").glob("*/*"))) == 1


def test_uninstaller_has_nothing_to_ask_without_kits(user_dir, monkeypatch):
    module = runpy.run_path(str(UNINSTALL))
    monkeypatch.setattr("builtins.input", lambda _p: pytest.fail("asked"))
    assert module["ask_kits"](Path.home()) == []


def test_session_start_sync_updates_due_kits_but_post_tool_use_does_not(
    repo, project, user_dir, capsys
):
    from meow.cli.sync_cli import _auto

    run(project, "add", str(repo.path), "--yes", "--name", "demo")
    repo.write({"skills/s/SKILL.md": SKILL.format(name="s", body="newer")})
    lock = user_dir / "kits.lock"
    data = json.loads(lock.read_text())
    data["kits"]["demo"]["checked_at"] = 0  # the 6 hour timer has run out
    lock.write_text(json.dumps(data))
    capsys.readouterr()
    _auto(project, "PostToolUse")
    assert "updated to" not in capsys.readouterr().out  # never the network
    _auto(project, "SessionStart")
    assert "kit demo: updated to" in capsys.readouterr().out
    assert "newer" in (Path.home() / ".claude/skills/s/SKILL.md").read_text()


def test_check_exits_nonzero_when_a_kit_is_behind(repo, project, user_dir, capsys):
    run(project, "add", str(repo.path), "--yes", "--name", "demo")
    run(project, "update", "--check")  # current: exit 0
    repo.write({"skills/s/SKILL.md": SKILL.format(name="s", body="newer")})
    with pytest.raises(SystemExit) as stopped:
        run(project, "update", "--check")
    assert stopped.value.code == 1
    assert "update available" in capsys.readouterr().out


def test_startup_keeps_reminding_until_a_project_kit_is_updated(
    repo, project, user_dir, capsys
):
    from meow.cli.sync_cli import _auto

    run(project, "add", str(repo.path), "--yes", "--name", "demo", "--scope", "project")
    repo.write({"skills/s/SKILL.md": SKILL.format(name="s", body="newer")})
    lock = project / ".meow/kits.lock"
    data = json.loads(lock.read_text())
    data["kits"]["demo"]["checked_at"] = 0
    lock.write_text(json.dumps(data))
    capsys.readouterr()
    for _ in range(2):  # the second start is inside the timer, yet still reminds
        _auto(project, "SessionStart")
        assert "Kit updates available" in capsys.readouterr().out
    run(project, "update", "demo")
    capsys.readouterr()
    _auto(project, "SessionStart")
    assert "Kit updates available" not in capsys.readouterr().out
