import subprocess
from pathlib import Path

import pytest

SKILL = "---\nname: {name}\ndescription: d\n---\n{body}\n"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=a@b", "-c", "user.name=a", *args],
        cwd=cwd, capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip


class KitRepo:
    """A local git repository standing in for a GitHub or GitLab kit."""

    def __init__(self, path: Path):
        self.path = path
        path.mkdir(parents=True)
        git(path, "init", "-q", "-b", "main")

    def write(self, files: dict[str, str]) -> str:
        for relative, text in files.items():
            target = self.path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        git(self.path, "add", "-A")
        git(self.path, "commit", "-q", "-m", "change")
        return git(self.path, "rev-parse", "HEAD")


@pytest.fixture
def make_kit(tmp_path):
    count = iter(range(100))

    def build(files: dict[str, str]) -> KitRepo:
        repo = KitRepo(tmp_path / f"kit{next(count)}")
        repo.write(files)
        return repo

    return build


@pytest.fixture
def project(tmp_path):
    path = tmp_path / "project"
    path.mkdir()
    git(path, "init", "-q")
    return path


@pytest.fixture
def user_dir(tmp_path):
    path = Path.home() / ".meow"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture(autouse=True)
def _no_leaked_pins(monkeypatch):
    """A run adopts kit pins in the process environment; never leak them."""
    monkeypatch.setenv("MEOW_KIT_PINS", "")
