import subprocess

import pytest

from meow.infrastructure.review_delta import changes_since, snapshot_tree


def git(repo, *argv):
    subprocess.run(["git", *argv], cwd=repo, check=True, capture_output=True)


def git_out(repo, *argv):
    return subprocess.run(
        ["git", *argv], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("one\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def test_delta_is_relative_to_the_previous_snapshot(repo):
    (repo / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    tree = snapshot_tree(repo)
    (repo / "a.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (repo / "new.txt").write_text("fresh\n", encoding="utf-8")
    delta = changes_since(repo, tree)
    assert delta.since == "previous review"
    assert "a.txt" in delta.stat
    assert "new.txt" in delta.stat
    assert "+three" in delta.diff
    assert "+two" not in delta.diff


def test_snapshot_leaves_index_untouched_and_skips_excluded(repo):
    (repo / "new.txt").write_text("fresh\n", encoding="utf-8")
    (repo / "report.md").write_text("verdict\n", encoding="utf-8")
    (repo / ".meow").mkdir()
    (repo / ".meow" / "x").write_text("x", encoding="utf-8")
    tree = snapshot_tree(repo, (repo / "report.md",))
    listing = git_out(repo, "ls-tree", "-r", "--name-only", tree).split()
    assert sorted(listing) == ["a.txt", "new.txt"]
    assert git_out(repo, "diff", "--cached", "--name-only") == ""


def test_unknown_tree_falls_back_to_head(repo):
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    delta = changes_since(repo, None)
    assert delta.since == "HEAD"
    assert "a.txt" in delta.stat
    assert "+changed" in delta.diff
    assert changes_since(repo, "0" * 40).since == "HEAD"


def test_non_repository_yields_empty_delta(tmp_path):
    assert snapshot_tree(tmp_path) is None
    delta = changes_since(tmp_path, None)
    assert (delta.stat, delta.diff) == ("", "")
