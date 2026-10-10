import subprocess

from meow.agents.base import ProjectContext
from meow.agents.reviewer import _git_review_context


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "app.py")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def test_review_context_includes_staged_changes(tmp_path):
    root = _repo(tmp_path)
    (root / "app.py").write_text("x = 2\n", encoding="utf-8")
    _git(root, "add", "app.py")

    context, has_diff = _git_review_context(ProjectContext(root, {}))

    assert has_diff
    assert "+x = 2" in context


def test_review_context_counts_new_untracked_files_as_changes(tmp_path):
    root = _repo(tmp_path)
    (root / "new.py").write_text("y = 1\n", encoding="utf-8")

    _, has_diff = _git_review_context(ProjectContext(root, {}))

    assert has_diff
