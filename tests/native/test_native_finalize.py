"""Native completion obeys the shared review and gate predicate."""

import asyncio
import subprocess
from unittest.mock import AsyncMock, patch

import pytest

from meow.execution.run_state import RunStore
from meow.native.native_state import checkpoint, finalize


def _review(root, run_id):
    from meow.native.native_prepare import verdict
    from meow.native.native_review import bind_review_checks, review_checks

    review_checks(root, root)
    report = root / ".meow" / "plans" / "report-review.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    bind_review_checks(report, root, root)
    report.write_text("SUMMARY: reviewed\nSTATUS: PASS\n", encoding="utf-8")
    assert verdict(report)["status"] == "PASS"
    checkpoint(
        root,
        root,
        "reviewer_finished",
        run_id=run_id,
        review_file=report,
        reviewer="PASS",
    )
    return report


@pytest.mark.parametrize("report_state", ["missing", "fail", "unbound"])
def test_native_checkpoint_rejects_unverified_pass_report(tmp_path, report_state):
    from meow.native.native_review import bind_review_checks

    subprocess.run(["git", "init", "-q", "-b", "dev", str(tmp_path)], check=True)
    run_id = checkpoint(tmp_path, tmp_path, "planned")["run_id"]
    report = tmp_path / ".meow" / "plans" / "report-review.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    if report_state != "missing":
        report.write_text(
            "SUMMARY: reviewed\nSTATUS: "
            + ("FAIL" if report_state == "fail" else "PASS")
            + "\n",
            encoding="utf-8",
        )
    if report_state != "unbound":
        bind_review_checks(report, tmp_path, tmp_path)

    with pytest.raises(ValueError, match=r"[Rr]eview"):
        checkpoint(
            tmp_path,
            tmp_path,
            "reviewer_finished",
            run_id=run_id,
            review_file=report,
            reviewer="PASS",
        )


@pytest.mark.parametrize("change", ["delete", "replace", "remove-binding"])
def test_native_finalize_revalidates_saved_report(tmp_path, change):
    subprocess.run(["git", "init", "-q", "-b", "dev", str(tmp_path)], check=True)
    run_id = checkpoint(tmp_path, tmp_path, "planned")["run_id"]
    report = _review(tmp_path, run_id)
    if change == "delete":
        report.unlink()
    elif change == "replace":
        report.write_text("SUMMARY: changed\nSTATUS: PASS\n", encoding="utf-8")
    else:
        report.with_suffix(report.suffix + ".checks.json").unlink()

    result = asyncio.run(finalize(tmp_path, tmp_path, run_id))

    assert result["complete"] is False
    assert "review" in result["reason"].lower()


@pytest.mark.parametrize("tester_state", ["missing", "legacy", "stale"])
def test_native_finalize_reports_missing_tester_before_running_checks(
    tmp_path, tester_state
):
    import json
    import sys

    subprocess.run(["git", "init", "-q", "-b", "dev", str(tmp_path)], check=True)
    (tmp_path / ".meow").mkdir()
    script = "from pathlib import Path; Path('ran-tests').touch()"
    (tmp_path / ".meow" / "config.toml").write_text(
        f"[[tester.tests]]\ncommand = {json.dumps(sys.executable)}\n"
        f"args = ['-c', {json.dumps(script)}]\n",
        encoding="utf-8",
    )
    run_id = checkpoint(tmp_path, tmp_path, "planned", request="work")["run_id"]
    if tester_state == "legacy":
        RunStore(tmp_path).transition(
            run_id, "tester_finished", results={"tester": "PASS"}
        )
    elif tester_state == "stale":
        RunStore(tmp_path).transition(
            run_id,
            "tester_finished",
            results={"tester": "PASS", "tester_revision": "old"},
        )
        (tmp_path / "changed.py").write_text("x = 1\n", encoding="utf-8")

    result = asyncio.run(finalize(tmp_path, tmp_path, run_id))

    assert not (tmp_path / "ran-tests").exists()
    assert result["complete"] is False
    assert result["required_mode"] == "cli"
    assert "tester" in result["reason"].lower()
    assert f"meow resume {run_id} --continue" in result["reason"]
    assert RunStore(tmp_path).load(run_id).last_failure == result["reason"]


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


def test_native_finalize_rejects_flag_only_tester_pass(tmp_path):
    import json
    import sys

    subprocess.run(["git", "init", "-q", "-b", "dev", str(tmp_path)], check=True)
    (tmp_path / ".meow").mkdir()
    script = "from pathlib import Path; Path('ran-tests').touch()"
    (tmp_path / ".meow" / "config.toml").write_text(
        f"[[tester.tests]]\ncommand = {json.dumps(sys.executable)}\n"
        f"args = ['-c', {json.dumps(script)}]\n",
        encoding="utf-8",
    )
    run_id = checkpoint(tmp_path, tmp_path, "planned", request="work")["run_id"]
    with pytest.raises(ValueError, match=r"tester.*CLI"):
        checkpoint(tmp_path, tmp_path, "tester_finished", run_id=run_id, tester="PASS")

    with patch("meow.native.native_state.deliver_verified_run"):
        result = asyncio.run(finalize(tmp_path, tmp_path, run_id))

    assert not (tmp_path / "ran-tests").exists()
    assert result["complete"] is False
    assert result["required_mode"] == "cli"


def test_native_finalize_delivers_after_current_evidence(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "dev", str(tmp_path)], check=True)
    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow" / "config.toml").write_text("", encoding="utf-8")
    run_id = checkpoint(tmp_path, tmp_path, "planned", request="work")["run_id"]
    _review(tmp_path, run_id)
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
    _review(tmp_path, run_id)
    with patch("meow.native.native_state.deliver_verified_run"):
        result = asyncio.run(finalize(tmp_path, tmp_path, run_id))
    assert result["complete"], RunStore(tmp_path).load(run_id).last_failure


def test_native_finalize_parks_when_a_required_check_times_out(tmp_path):
    from meow.execution.run_failures import TransientRunError
    from meow.execution.run_state import CheckResult

    subprocess.run(["git", "init", "-q", "-b", "dev", str(tmp_path)], check=True)
    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow" / "config.toml").write_text("", encoding="utf-8")
    run_id = checkpoint(tmp_path, tmp_path, "planned", request="work")["run_id"]
    _review(tmp_path, run_id)
    timed_out = CheckResult("lint", "x", True, None, 1.0, "", "r", "f", True)
    with (
        patch(
            "meow.native.native_state.run_final_checks",
            new_callable=AsyncMock,
            return_value=[timed_out],
        ),
        pytest.raises(TransientRunError),
    ):
        asyncio.run(finalize(tmp_path, tmp_path, run_id))
    record = RunStore(tmp_path).load(run_id)
    assert record.phase == "parked"
