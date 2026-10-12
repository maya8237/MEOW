import subprocess
import sys

from meow.infrastructure import console


def test_children_get_no_window_flag_on_windows(monkeypatch):
    seen = []

    class FakePopen:
        def __init__(self, *args, creationflags=0, **kwargs):
            seen.append(creationflags)

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NEW_CONSOLE", 0x10, raising=False)
    monkeypatch.setattr(subprocess, "DETACHED_PROCESS", 0x8, raising=False)
    monkeypatch.setattr(console, "_VISIBLE_FLAGS", 0x08000000 | 0x10 | 0x8)
    monkeypatch.setattr(console.os, "name", "nt")

    console.hide_child_consoles()
    console.hide_child_consoles()  # idempotent: no double wrapping
    subprocess.Popen(["git"])
    subprocess.Popen(["git"], creationflags=0x200)
    subprocess.Popen(["git"], creationflags=0x10)

    assert seen == [0x08000000, 0x200 | 0x08000000, 0x10]


def test_noop_off_windows():
    if sys.platform == "win32":
        return
    original = subprocess.Popen.__init__
    console.hide_child_consoles()
    assert subprocess.Popen.__init__ is original
