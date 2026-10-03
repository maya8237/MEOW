"""Task commits combine without discarding failed worker evidence."""

import subprocess

from meow.tasks.integration import integrate_results
from meow.tasks.scheduler import TaskOutcome


def _git(repo, *args):
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README")
    _git(repo, "commit", "-m", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


def _task_commit(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
    repo, base, branch, name, content
):
    _git(repo, "checkout", "-b", branch, base)
    (repo / name).write_text(content, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-m", branch)
    return _git(repo, "rev-parse", "HEAD")


def test_disjoint_task_commits_integrate(tmp_path):
    repo, base = _repo(tmp_path)
    a = _task_commit(repo, base, "api", "api.txt", "api")
    b = _task_commit(repo, base, "ui", "ui.txt", "ui")
    _git(repo, "checkout", "-b", "integration", base)
    result = integrate_results(
        repo,
        base,
        (
            TaskOutcome("api", "complete", str(repo), a),
            TaskOutcome("ui", "complete", str(repo), b),
        ),
    )
    assert result.conflict is None
    assert len(result.applied) == len((a, b))
    assert (repo / "api.txt").read_text() == "api"
    assert (repo / "ui.txt").read_text() == "ui"


def test_failed_worker_blocks_integration(tmp_path):
    repo, base = _repo(tmp_path)
    result = integrate_results(
        repo, base, (TaskOutcome("api", "failed", str(repo), None, "agent failed"),)
    )
    assert result.conflict == "api did not complete"
    assert _git(repo, "rev-parse", "HEAD") == base


def test_conflict_preserves_worker_commits_and_aborts_cherry_pick(tmp_path):
    repo, base = _repo(tmp_path)
    a = _task_commit(repo, base, "first", "README", "first\n")
    b = _task_commit(repo, base, "second", "README", "second\n")
    _git(repo, "checkout", "-b", "integration", base)
    result = integrate_results(
        repo,
        base,
        (
            TaskOutcome("first", "complete", str(repo), a),
            TaskOutcome("second", "complete", str(repo), b),
        ),
    )
    assert result.applied == ("first",)
    assert "second" in result.conflict
    assert _git(repo, "status", "--porcelain") == ""
    assert _git(repo, "rev-parse", "first") == a
    assert _git(repo, "rev-parse", "second") == b
