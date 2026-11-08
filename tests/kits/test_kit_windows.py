"""Behavior that matters on Windows, checked in ways Linux can reproduce."""

import os
import stat
import subprocess
from pathlib import Path

import pytest

from meow.project.custom import kit_store
from meow.project.custom.kit_store import default_name, fetch, remove_tree
from meow.project.custom.kits import read_lock, write_lock

from .conftest import SKILL


def test_remove_tree_clears_read_only_files(tmp_path):
    tree = tmp_path / "repo" / ".git" / "objects"
    tree.mkdir(parents=True)
    locked = tree / "pack-1.idx"
    locked.write_text("x")
    locked.chmod(stat.S_IREAD)
    remove_tree(tmp_path / "repo")
    assert not (tmp_path / "repo").exists()


def test_retry_handler_makes_the_file_writable_then_retries(tmp_path):
    locked = tmp_path / "pack.idx"
    locked.write_text("x")
    locked.chmod(stat.S_IREAD)
    removed = []
    kit_store._make_writable_and_retry(removed.append, str(locked), PermissionError())
    assert removed == [str(locked)]
    assert os.access(locked, os.W_OK) or os.geteuid() == 0  # root ignores modes
    with pytest.raises(FileNotFoundError):  # other errors are not swallowed
        kit_store._make_writable_and_retry(
            removed.append, str(locked), FileNotFoundError()
        )


def test_remove_tree_quiet_ignores_failures(tmp_path, monkeypatch):
    def broken(*_args, **_kwargs):
        raise PermissionError("held open by an antivirus scan")

    (tmp_path / "x").mkdir()
    monkeypatch.setattr(kit_store.shutil, "rmtree", broken)
    remove_tree(tmp_path / "x", quiet=True)
    with pytest.raises(PermissionError):
        remove_tree(tmp_path / "x")


@pytest.mark.parametrize(
    ("location", "name"),
    [
        ("C:\\Users\\me\\kits\\My_Kit", "my-kit"),
        ("C:\\work\\team-kit.git", "team-kit"),
        ("C:/work/kits/Tools", "tools"),
        ("\\\\server\\share\\acme-kit", "acme-kit"),
        ("git@gitlab.example.com:team/kit.git", "kit"),
    ],
)
def test_default_name_handles_windows_paths(location, name):
    assert default_name(location) == name


def test_junction_in_a_kit_is_rejected(make_kit, tmp_path, monkeypatch):
    repo = make_kit({"skills/s/SKILL.md": SKILL.format(name="s", body="x")})
    sha = repo.write({"skills/evil/SKILL.md": "x"})
    real = Path.is_junction
    monkeypatch.setattr(
        Path, "is_junction", lambda self: self.name == "evil" or real(self)
    )
    with pytest.raises(ValueError, match="symbolic link"):
        fetch(tmp_path / "cache", str(repo.path), sha)


def test_git_never_opens_a_login_window_or_prompt(make_kit, monkeypatch):
    repo = make_kit({"skills/a/SKILL.md": SKILL.format(name="a", body="x")})
    seen = {}
    real = subprocess.run

    def spy(*args, **kwargs):
        seen.update(kwargs.get("env", {}))
        return real(*args, **kwargs)

    monkeypatch.setattr(kit_store.subprocess, "run", spy)
    kit_store.remote_sha(str(repo.path))
    assert seen["GIT_TERMINAL_PROMPT"] == "0"
    assert seen["GCM_INTERACTIVE"] == "never"
    assert seen["GIT_ALLOW_PROTOCOL"] == "file:git:https:ssh"


def test_lock_write_is_atomic_and_leaves_no_temp_files(tmp_path, monkeypatch):
    lock = tmp_path / "kits.lock"
    write_lock(lock, {"a": {"sha": "1"}})
    assert read_lock(lock) == {"a": {"sha": "1"}}
    assert [p.name for p in tmp_path.iterdir()] == ["kits.lock"]

    def blocked(*_args, **_kwargs):
        raise PermissionError("sharing violation")

    monkeypatch.setattr("meow.project.custom.kits.replace_with_retry", blocked)
    with pytest.raises(PermissionError):
        write_lock(lock, {"b": {"sha": "2"}})
    assert read_lock(lock) == {"a": {"sha": "1"}}  # the old lock survives intact
    assert [p.name for p in tmp_path.iterdir()] == ["kits.lock"]
