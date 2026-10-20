import contextlib
import json
import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from meow.hooks.claude import install_claude_hooks
from meow.project.onboarding import BOUNDARY

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "uninstall.py"


@pytest.mark.parametrize("purge", [False, True])
def test_uninstall_preserves_other_settings_and_is_repeatable(tmp_path, purge):
    module = runpy.run_path(str(SCRIPT))
    root = tmp_path / ".meow"
    for name in (
        "bin/meow",
        "versions/0.1.5/runtime/python",
        "current-version",
        "config.toml",
        "plans/keep.md",
    ):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("keep")
    settings = tmp_path / ".claude/settings.json"
    settings.parent.mkdir()
    settings.write_text(
        json.dumps({
            "enabledPlugins": {"meow@meow": True, "other@other": True},
            "extraKnownMarketplaces": {"meow": {}, "other": {}},
            "permissions": {"allow": ["Read"]},
        })
    )
    profile = tmp_path / ".bashrc"
    profile.write_text('echo hello\nexport PATH="$HOME/.meow/bin:$PATH"\n')
    # Exercise profile cleanup on every host without touching the real registry.
    module["uninstall"].__globals__["remove_path"] = module["remove_profiles"]
    module["uninstall"](tmp_path, purge=purge)
    module["uninstall"](tmp_path, purge=purge)
    assert not (root / "bin").exists()
    assert not (root / "versions").exists()
    assert not (root / "current-version").exists()
    assert (root / "config.toml").exists() is not purge
    assert (root / "plans/keep.md").exists() is not purge
    assert profile.read_text() == "echo hello\n"
    assert json.loads(settings.read_text()) == {
        "enabledPlugins": {"other@other": True},
        "extraKnownMarketplaces": {"other": {}},
        "permissions": {"allow": ["Read"]},
    }


def test_uninstall_stops_before_deleting_when_settings_are_invalid(tmp_path):
    module = runpy.run_path(str(SCRIPT))
    binary = tmp_path / ".meow/bin/meow"
    binary.parent.mkdir(parents=True)
    binary.write_text("keep")
    settings = tmp_path / ".claude/settings.json"
    settings.parent.mkdir()
    settings.write_text("{invalid")
    with pytest.raises(ValueError):
        module["uninstall"](tmp_path)
    assert binary.exists()
    assert settings.read_text() == "{invalid"


def test_un_onboard_removes_project_setup_and_preserves_other_hooks(tmp_path):
    module = runpy.run_path(str(SCRIPT))
    project = tmp_path / "project"
    project.mkdir()
    (project / ".gitignore").write_text("dist/\n" + "\n".join(BOUNDARY) + "\n")
    install_claude_hooks(project, ["lint", "plan_stop"])
    settings = project / ".claude/settings.local.json"
    data = json.loads(settings.read_text())
    other = {"hooks": [{"type": "command", "command": "echo keep"}]}
    data["hooks"]["Stop"].append(other)
    data["enabledPlugins"] = {"meow-local@meow-local": True, "other@other": True}
    settings.write_text(json.dumps(data))
    (project / ".meow/config.toml").write_text("max_rounds = 8")
    module["un_onboard"](project)
    module["un_onboard"](project)
    assert not (project / ".meow").exists()
    assert (project / ".gitignore").read_text() == (
        "dist/\n.claude/settings.local.json\n"
    )
    data = json.loads(settings.read_text())
    assert data["hooks"]["Stop"] == [other]
    assert data["enabledPlugins"] == {"other@other": True}


@pytest.mark.skipif(os.name != "nt", reason="Windows registry PATH")
def test_windows_path_preserves_other_entries_and_registry_type(tmp_path, monkeypatch):
    import ctypes

    module = runpy.run_path(str(SCRIPT))
    writes = []
    registry = SimpleNamespace(
        HKEY_CURRENT_USER=1,
        KEY_QUERY_VALUE=1,
        KEY_SET_VALUE=2,
        OpenKey=lambda *_: contextlib.nullcontext("key"),
        QueryValueEx=lambda *_: (f"C:\\other;{tmp_path / '.meow/bin'};C:\\keep", 2),
        SetValueEx=lambda *args: writes.append(args),
    )
    monkeypatch.setitem(sys.modules, "winreg", registry)
    monkeypatch.setattr(ctypes.windll.user32, "SendMessageTimeoutW", lambda *_: 1)
    module["remove_path"](tmp_path)
    assert writes == [("key", "Path", 0, 2, "C:\\other;C:\\keep")]


def test_refuses_to_delete_outside_install_root(tmp_path):
    module = runpy.run_path(str(SCRIPT))
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(ValueError, match="outside"):
        module["remove_install"](outside, tmp_path / ".meow")
    assert outside.is_dir()


@pytest.mark.parametrize("extension", ["ps1", "sh"])
def test_script_entry_points_run_without_parameters(tmp_path, extension):
    shell = shutil.which("powershell" if extension == "ps1" else "sh")
    git_bash = (
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Git/bin/bash.exe"
    )
    if extension == "sh" and not shell and git_bash.is_file():
        shell = str(git_bash)
    if not shell:
        pytest.skip(f"No shell for {extension}")
    wrapper = tmp_path / f"uninstall.{extension}"
    shutil.copyfile(SCRIPT.with_suffix(f".{extension}"), wrapper)
    (tmp_path / "uninstall.py").write_text(
        "import json, sys; print(json.dumps(sys.argv[1:]))"
    )
    command = [shell, wrapper.as_posix()]
    if extension == "ps1":
        quoted = str(wrapper).replace("'", "''")
        command = [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f"& '{quoted}'",
        ]
    result = subprocess.run(
        command,
        env={**os.environ, "MEOW_PYTHON": sys.executable},
        capture_output=True,
        text=True,
        check=True,
    )
    assert "( o.o )   meow" in result.stdout
    assert json.loads(result.stdout.splitlines()[-1]) == []


@pytest.mark.parametrize("confirm", ["y", "n"])
def test_interactive_uninstall_only_changes_files_after_confirmation(
    tmp_path, monkeypatch, confirm
):
    module = runpy.run_path(str(SCRIPT))
    replies = iter(["y", str(tmp_path), "", confirm])
    calls = []
    monkeypatch.setattr("builtins.input", lambda _: next(replies))
    module["main"].__globals__.update(
        uninstall=lambda home, **kw: calls.append(("uninstall", kw["purge"])),
        un_onboard=lambda project: calls.append(("project", project)),
    )
    assert module["main"]() == 0
    assert calls == (
        [("project", tmp_path), ("uninstall", True)] if confirm == "y" else []
    )
