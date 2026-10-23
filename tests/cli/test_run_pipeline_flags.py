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


def test_fix_non_meow_jobs_rejects_in_place_run(capsys):
    args = parse("x", "-np", "--fix-non-meow-jobs")
    with pytest.raises(SystemExit):
        validation._validate_run_flags(_build_arg_parser(), args)
    assert "--fix-non-meow-jobs requires a worktree run" in capsys.readouterr().err


def test_fix_non_meow_jobs_uses_inferred_target():
    validation._validate_run_flags(
        _build_arg_parser(), parse("x", "--fix-non-meow-jobs")
    )


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
    store = RunStore(Path(options.get("record_root", repo)))
    return store.latest() if run_id is None else store.load(run_id)


def test_delivery_records_base_and_overrides(tmp_path):
    repo = _repo(tmp_path)
    record = _run(repo, source_branch="main", pipeline_options={"open_mr": False})
    assert record.delivery["base"] == "master"
    assert record.delivery["source_branch"] == "main"
    assert record.delivery["pipeline_options"] == {"open_mr": False}


def test_resume_without_options_keeps_earlier_base(tmp_path):
    repo = _repo(tmp_path)
    first = _run(repo, source_branch="main", pipeline_options={"open_mr": False})
    again = _run(repo, run_id=first.id)
    assert again.delivery["base"] == "master"
    assert again.delivery["pipeline_options"] == {"open_mr": False}
    assert "enabled" in again.delivery


@pytest.mark.parametrize("branch", ["dev", "feature/starting-point"])
def test_current_checkout_does_not_override_default_target(tmp_path, branch):
    repo = _repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "switch", "-c", branch], check=True)
    record = _run(repo)
    assert record.delivery["base"] == ("dev" if branch == "dev" else "master")
    assert Path(record.worktree) != repo


def test_detached_origin_defaults_to_master(tmp_path):
    repo = _repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "checkout", "--detach"], check=True)
    assert _run(repo).delivery["base"] == "master"


def test_from_does_not_choose_the_merge_target(tmp_path):
    repo = _repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "branch", "release"], check=True)
    assert _run(repo, source_branch="release").delivery["base"] == "master"


@pytest.mark.parametrize("commit,push", [(False, False), (True, False), (True, True)])
def test_in_place_runs_do_not_open_merge_requests(tmp_path, commit, push):
    record = _run(_repo(tmp_path), use_worktree=False, commit=commit, push=push)
    assert not record.delivery.get("base")


def test_target_is_saved_before_preparation_can_fail(tmp_path):
    repo = _repo(tmp_path)
    with (
        patch.object(sprint_runner, "_prepare_sprint", side_effect=Stop),
        pytest.raises(Stop),
    ):
        asyncio.run(sprint_runner.run_sprint(repo, "feat", "x"))
    assert RunStore(repo).latest().delivery["base"] == "master"


def test_resume_in_feature_worktree_keeps_origin_target(tmp_path):
    repo = _repo(tmp_path)
    first = _run(repo)
    worktree = Path(first.worktree)
    subprocess.run(
        ["git", "-C", str(worktree), "switch", "-c", "feature-result"], check=True
    )
    subprocess.run(["git", "-C", str(repo), "switch", "-c", "other-branch"], check=True)
    again = _run(worktree, run_id=first.id, record_root=repo, use_worktree=False)
    assert again.delivery["base"] == "master"
    assert Path(again.worktree) == worktree


@pytest.mark.parametrize(
    "explicit,has_dev", [(False, False), (False, True), (True, True)]
)
def test_run_reuses_linked_worktree_and_saves_target(tmp_path, explicit, has_dev):
    repo = _repo(tmp_path / "repo")
    linked = tmp_path / "linked"
    subprocess.run(["git", "-C", str(repo), "branch", "release"], check=True)
    if has_dev:
        subprocess.run(["git", "-C", str(repo), "branch", "dev"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "worktree",
            "add",
            "-b",
            "feature-existing",
            str(linked),
        ],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(linked), "commit", "--allow-empty", "-m", "dev commit"],
        check=True,
    )
    record = _run(linked, **({"target_branch": "release"} if explicit else {}))
    assert record.delivery["base"] == (
        "release" if explicit else "dev" if has_dev else "master"
    )
    assert Path(record.worktree) == linked
    assert record.branch == "feature-existing"


def test_detached_origin_prefers_dev_when_available(tmp_path):
    repo = _repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "branch", "dev"], check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "--detach"], check=True)
    assert _run(repo).delivery["base"] == "dev"


def test_target_can_differ_from_starting_branch_and_survives_resume(tmp_path):
    repo = _repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "branch", "release"], check=True)
    record = _run(repo, source_branch="release", target_branch="dev")
    assert record.delivery["base"] == "dev"
    assert record.delivery["source_branch"] == "release"
    again = _run(
        Path(record.worktree), run_id=record.id, record_root=repo, use_worktree=False
    )
    assert again.delivery["base"] == "dev"
    assert again.delivery["source_branch"] == "release"


def test_target_flag_is_dispatched_separately_from_from(tmp_path):
    with patch("meow.cli.dispatch.run_sprint", new=AsyncMock()) as run:
        _dispatch_plain_build(
            parse("x", "--from", "release", "--target", "dev"),
            tmp_path,
            use_worktree=True,
        )
    assert run.await_args.kwargs["source_branch"] == "release"
    assert run.await_args.kwargs["target_branch"] == "dev"


def test_only_explicit_target_overrides_configured_target(tmp_path):
    repo = _repo(tmp_path)
    (repo / ".meow").mkdir()
    (repo / ".meow/config.toml").write_text(
        '[delivery]\ntarget_branch = "integration"\n', encoding="utf-8"
    )
    assert _run(repo).delivery["base"] == "integration"
    assert _run(repo, source_branch="main").delivery["base"] == "integration"
    assert (
        _run(repo, source_branch="main", target_branch="release").delivery["base"]
        == "release"
    )


def test_background_target_saved_before_worker_starts(tmp_path, monkeypatch):
    from meow.infrastructure import background

    repo = _repo(tmp_path)
    monkeypatch.setattr(background, "_spawn_worker", lambda *_args: None)
    rid = background.launch_background(
        repo,
        ["run", "x", "--background", "--from", "main", "--target", "dev", "--no-pr"],
    )
    delivery = RunStore(repo).load(rid).delivery
    assert delivery["base"] == "dev"
    assert delivery["source_branch"] == "main"
    assert delivery["pipeline_options"] == {"open_mr": False}


def test_config_change_does_not_retarget_resume(tmp_path):
    repo = _repo(tmp_path)
    first = _run(repo)
    (repo / ".meow/config.toml").write_text(
        '[delivery]\ntarget_branch = "new-default"\n', encoding="utf-8"
    )
    again = _run(
        Path(first.worktree), run_id=first.id, record_root=repo, use_worktree=False
    )
    assert again.delivery["base"] == "master"


@pytest.mark.parametrize(
    "argv",
    [["x", "-n", "--target", "dev"], ["x", "--target="], ["x", "--target=bad branch"]],
)
def test_invalid_target_options_are_rejected(argv):
    with pytest.raises(SystemExit):
        validation._validate_run_flags(_build_arg_parser(), parse(*argv))
