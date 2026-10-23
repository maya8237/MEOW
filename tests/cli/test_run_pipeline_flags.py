"""`meow run --from` pipeline flags: validation, dispatch and journaled delivery."""

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from meow.cli import validation
from meow.cli.dispatch import _dispatch_plain_build
from meow.cli.parser import _build_arg_parser
from meow.execution import sprint_runner
from meow.execution.run_state import RunStore


class Stop(Exception):
    pass


def parse(*argv: str):
    return _build_arg_parser().parse_args(["run", *argv])


def test_fix_non_meow_jobs_requires_from(capsys):
    args = parse("x", "--fix-non-meow-jobs")
    with pytest.raises(SystemExit):
        validation._validate_run_flags(_build_arg_parser(), args)
    assert "--fix-non-meow-jobs requires --from" in capsys.readouterr().err


def test_fix_non_meow_jobs_with_from_validates():
    args = parse("x", "--from", "main", "--fix-non-meow-jobs")
    validation._validate_run_flags(_build_arg_parser(), args)
    assert args.fix_non_meow_jobs is True


def test_no_pr_allowed_on_plain_run():
    args = parse("x", "--from", "main", "--no-pr")
    validation._validate_run_flags(_build_arg_parser(), args)


@pytest.mark.parametrize("flag", ["--dry-run", "--max-parallel=2"])
def test_orchestration_only_flags_still_need_orchestrate(flag, capsys):
    args = parse("x", flag)
    with pytest.raises(SystemExit):
        validation._validate_run_flags(_build_arg_parser(), args)
    assert "require --orchestrate" in capsys.readouterr().err


def test_dispatch_passes_only_user_set_overrides(tmp_path):
    cases = (
        (["x"], None),
        (["x", "--from", "main", "--no-pr"], {"open_mr": False}),
        (
            ["x", "--from", "main", "--fix-non-meow-jobs"],
            {"fix_non_meow_jobs": True},
        ),
        (
            ["x", "--from", "main", "--no-pr", "--fix-non-meow-jobs"],
            {"open_mr": False, "fix_non_meow_jobs": True},
        ),
    )
    for argv, expected in cases:
        with patch("meow.cli.dispatch.run_sprint", new=AsyncMock()) as run:
            _dispatch_plain_build(parse(*argv), tmp_path, use_worktree=True)
        assert run.await_args.kwargs.get("pipeline_options") == expected


def _repo(path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "T"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "t@e.c"], check=True
    )
    (path / "f").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "f"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "i"], check=True)
    return path


def _run(repo: Path, run_id=None, **options):
    with (
        patch.object(sprint_runner, "_run_onboarding", side_effect=Stop),
        pytest.raises(Stop),
    ):
        asyncio.run(
            sprint_runner.run_sprint(repo, "feat", "x", run_id=run_id, **options)
        )
    return RunStore(repo).latest() if run_id is None else RunStore(repo).load(run_id)


def test_delivery_records_base_and_overrides(tmp_path):
    repo = _repo(tmp_path)
    record = _run(repo, source_branch="main", pipeline_options={"open_mr": False})
    assert record.delivery["base"] == "main"
    assert record.delivery["pipeline_options"] == {"open_mr": False}


def test_resume_without_options_keeps_earlier_base(tmp_path):
    repo = _repo(tmp_path)
    first = _run(repo, source_branch="main", pipeline_options={"open_mr": False})
    again = _run(repo, run_id=first.id)
    assert again.delivery["base"] == "main"
    assert again.delivery["pipeline_options"] == {"open_mr": False}
    assert "enabled" in again.delivery
