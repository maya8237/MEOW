"""Native completion obeys the shared review and gate predicate."""

import asyncio
import subprocess
from unittest.mock import AsyncMock, patch

from meow.execution.run_state import RunStore
from meow.native.native_state import checkpoint, finalize


def test_native_finalize_requires_review(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "dev", str(tmp_path)], check=True)
    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow" / "config.toml").write_text(
        '[[lint]]\ncommand="ruff check"\n', encoding="utf-8"
    )
    run_id = checkpoint(tmp_path, tmp_path, "planned", request="work")["run_id"]
    with (
        patch(
            "meow.native.native_state.run_final_checks",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch("meow.native.native_state.configured_checks", return_value=[]),
    ):
        result = asyncio.run(finalize(tmp_path, tmp_path, run_id))
    assert not result["complete"]
    assert RunStore(tmp_path).load(run_id).phase == "failed"


def test_native_finalize_delivers_after_current_evidence(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "dev", str(tmp_path)], check=True)
    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow" / "config.toml").write_text(
        '[[lint]]\ncommand="ruff check"\n', encoding="utf-8"
    )
    run_id = checkpoint(tmp_path, tmp_path, "planned", request="work")["run_id"]
    with (
        patch(
            "meow.native.native_state.run_final_checks",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch("meow.native.native_state.configured_checks", return_value=[]),
        patch("meow.native.native_state.completion_ready", return_value=True),
        patch("meow.native.native_state.deliver_verified_run") as deliver,
    ):
        result = asyncio.run(finalize(tmp_path, tmp_path, run_id))
    assert result["complete"]
    deliver.assert_called_once()


def test_native_finalize_loads_repository_config_for_worktree(tmp_path):
    repo = tmp_path / "repo"
    active = tmp_path / "worktree"
    repo.mkdir()
    active.mkdir()
    (repo / ".meow").mkdir()
    (repo / ".meow" / "config.toml").write_text("max_rounds = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "dev", str(repo)], check=True)
    run_id = checkpoint(repo, active, "planned", request="work")["run_id"]
    config = {"tester": {"tests": []}}

    with (
        patch("meow.cli.resume_cli._validate", return_value=None),
        patch("meow.native.native_state.load_config", return_value=config) as load,
        patch(
            "meow.native.native_state.run_final_checks",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch("meow.native.native_state.configured_checks", return_value=[]),
        patch("meow.native.native_state.completion_ready", return_value=False),
    ):
        result = asyncio.run(finalize(repo, active, run_id))

    load.assert_called_once_with(repo)
    assert not result["complete"]


def test_native_finalize_tolerates_files_written_by_checks(tmp_path):
    import sys

    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*git, "init", "-q", "-b", "dev", str(tmp_path)], check=True)
    (tmp_path / "source.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / ".meow").mkdir()
    code = "open('report.xml', 'w').write('<ok/>')"
    (tmp_path / ".meow" / "config.toml").write_text(
        f"[[build]]\ncommand = {sys.executable!r}\nargs = ['-c', {code!r}]\n",
        encoding="utf-8",
    )
    subprocess.run([*git, "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run([*git, "-C", str(tmp_path), "commit", "-qm", "base"], check=True)
    run_id = checkpoint(tmp_path, tmp_path, "planned", request="work")["run_id"]
    checkpoint(tmp_path, tmp_path, "reviewer_finished", reviewer="PASS", run_id=run_id)
    with patch("meow.native.native_state.deliver_verified_run"):
        result = asyncio.run(finalize(tmp_path, tmp_path, run_id))
    assert result["complete"], RunStore(tmp_path).load(run_id).last_failure
