"""Local review gates must describe the current checkout and survive launchers."""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from meow.infrastructure.checks import Check
from meow.project.config_models import LintCommand, VerificationCommand


def _api():
    from meow.infrastructure import review_verification

    return review_verification


def _config(kind, code, *, required=True):
    args = ("-c", code)
    command = sys.executable
    if kind == "lint":
        return {"lint": [LintCommand(command, args=args, gate=required)]}
    if kind == "test":
        return {
            "tester": {
                "tests": [VerificationCommand(Path("."), command, args, gate=required)]
            }
        }
    return {"build": [Check("build", "build-1", command, args, required)]}


@pytest.mark.parametrize("kind", ["lint", "test", "build"])
def test_required_failure_blocks_review_and_keeps_output(tmp_path, kind):
    api = _api()
    config = _config(kind, "print('verification broke'); raise SystemExit(3)")
    assert not api.review_check_status(tmp_path, config)["passed"]
    evidence = asyncio.run(api.review_evidence(tmp_path, config))
    assert evidence.blocking_failed
    assert "verification broke" in evidence.report()
    status = api.review_check_status(tmp_path, config)
    assert status["status"] == "failed"
    assert status["current"] and not status["passed"]
    assert Path(status["result_path"]).is_file()
    assert "verification broke" in Path(status["log_path"]).read_text()


@pytest.mark.parametrize("kind", ["lint", "test", "build"])
def test_advisory_failure_does_not_block(tmp_path, kind):
    api = _api()
    config = _config(kind, "print('advisory'); raise SystemExit(1)", required=False)
    evidence = asyncio.run(api.review_evidence(tmp_path, config))
    assert not evidence.blocking_failed
    assert "advisory" in "".join(evidence.informational)
    assert api.review_check_status(tmp_path, config)["passed"]


def test_success_reused_until_source_or_config_changes(tmp_path):
    api = _api()
    source = tmp_path / "source.py"
    source.write_text("original")
    config = _config(
        "build",
        "from pathlib import Path; p=Path('.meow/count'); "
        "p.write_text(str(int(p.read_text())+1) if p.exists() else '1')",
    )
    for _ in range(2):
        assert not asyncio.run(api.review_evidence(tmp_path, config)).blocking_failed
    assert (tmp_path / ".meow/count").read_text() == "1"
    source.write_text("changed")
    assert api.review_check_status(tmp_path, config)["status"] == "stale"
    asyncio.run(api.review_evidence(tmp_path, config))
    assert (tmp_path / ".meow/count").read_text() == "2"
    config["lint_timeout"] = 123
    assert not api.review_check_status(tmp_path, config)["passed"]


def test_unlaunchable_required_test_is_failed_evidence(tmp_path):
    api = _api()
    config = {
        "tester": {
            "tests": [VerificationCommand(Path("."), "meow-no-such-test-executable")]
        }
    }
    assert asyncio.run(api.review_evidence(tmp_path, config)).blocking_failed
    assert api.review_check_status(tmp_path, config)["status"] == "failed"


def test_empty_config_without_disk_config_passes(tmp_path):
    api = _api()
    assert not asyncio.run(api.review_evidence(tmp_path, {})).blocking_failed
    assert api.review_check_status(tmp_path, {})["passed"]


def test_background_survives_launcher_and_deduplicates(tmp_path):
    api = _api()
    directory = tmp_path / ".meow"
    directory.mkdir()
    # A slow command makes the launched process observable after its caller exits.
    (directory / "config.toml").write_text(
        "[[build]]\ncommand = "
        + json.dumps(sys.executable)
        + '\nargs = ["-c", '
        + json.dumps("import time; time.sleep(1.5); print('durable verification')")
        + "]\n"
    )
    launch = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json,sys; from pathlib import Path; "
            "from meow.infrastructure.review_verification import start_review_checks; "
            "print(json.dumps(start_review_checks(Path(sys.argv[1]), "
            "Path(sys.argv[1]))))",
            str(tmp_path),
        ],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=True,
    )
    first = json.loads(launch.stdout)
    second = api.start_review_checks(tmp_path, tmp_path)
    assert second["job_id"] == first["job_id"]
    from meow.project.config import load_config

    config = load_config(tmp_path)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        status = api.review_check_status(tmp_path, config)
        if status["status"] not in {"starting", "running"}:
            break
        time.sleep(0.1)
    assert status["passed"], status
    assert "durable verification" in Path(status["log_path"]).read_text()
    assert (
        json.loads(Path(status["result_path"]).read_text())["results"][0]["exit_code"]
        == 0
    )


def test_dead_worker_never_counts_as_pass(tmp_path):
    api = _api()
    config = _config("build", "pass")
    asyncio.run(api.review_evidence(tmp_path, config))
    status = api.review_check_status(tmp_path, config)
    path = Path(status["state_path"])
    state = json.loads(path.read_text())
    state.update(status="running", pid=99999999, start_identity="dead")
    path.write_text(json.dumps(state))
    status = api.review_check_status(tmp_path, config)
    assert status["status"] == "interrupted"
    assert not status["passed"]


def test_documents_in_legacy_docs_dir_invalidate_checks_but_reports_do_not(tmp_path):
    api = _api()
    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "GUIDE.md"
    source.write_text("original")
    config = {**_config("build", "pass"), "docs_dir": "docs"}
    asyncio.run(api.review_evidence(tmp_path, config))
    (docs / "prompt.00000000.review.md").write_text("STATUS: PASS")
    assert api.review_check_status(tmp_path, config)["passed"]
    source.write_text("changed")
    assert not api.review_check_status(tmp_path, config)["passed"]


def test_unchanged_failed_checks_can_be_inspected_without_running_twice(tmp_path):
    api = _api()
    config = _config("build", "raise SystemExit(1)")
    asyncio.run(api.review_evidence(tmp_path, config))
    first = api.review_check_status(tmp_path, config)["job_id"]
    asyncio.run(api.review_evidence(tmp_path, config, retry_failed=False))
    assert api.review_check_status(tmp_path, config)["job_id"] == first


def test_starting_live_worker_is_not_relaunched_after_startup_grace(
    tmp_path, monkeypatch
):
    api = _api()
    config = _config("build", "pass")
    state, _ = api._reserve(tmp_path, api._normalized(config))
    api._register_worker(tmp_path, state, os.getpid())
    monkeypatch.setattr(api.time, "time", lambda: state["started_at"] + 60)
    status, reserved = api._reserve(tmp_path, api._normalized(config))
    assert not reserved
    assert status["active"]


def test_review_evidence_parks_on_a_timed_out_required_check(tmp_path, monkeypatch):
    from meow.execution.run_failures import TransientRunError
    from meow.execution.run_state import CheckResult

    api = _api()
    config = _config("build", "pass")
    timed_out = CheckResult("build", "x", True, None, 1.0, "", "r", "f", True)

    fake = AsyncMock(return_value=[timed_out])

    monkeypatch.setattr(api, "run_final_checks", fake)
    with pytest.raises(TransientRunError) as caught:
        asyncio.run(api.review_evidence(tmp_path, config))
    assert caught.value.cause == "check_timeout"


def test_status_with_a_timed_out_check_survives_json(tmp_path, monkeypatch):
    from meow.execution.run_failures import TransientRunError
    from meow.execution.run_state import CheckResult
    from meow.native.native_review import bind_review_checks, review_checks

    api = _api()
    config = _config("build", "pass")
    timed_out = CheckResult("build", "x", True, None, 1.0, "", "r", "f", True)
    monkeypatch.setattr(api, "run_final_checks", AsyncMock(return_value=[timed_out]))
    monkeypatch.setattr("meow.native.native_review.load_config", lambda _root: config)
    with pytest.raises(TransientRunError):
        asyncio.run(api.review_evidence(tmp_path, config))
    status = api.review_check_status(tmp_path, config)
    assert status["timed_out"]
    json.dumps(status)
    json.dumps(review_checks(tmp_path, tmp_path, status=True))
    report = tmp_path / "r-review.md"
    json.dumps(bind_review_checks(report, tmp_path, tmp_path))
    with pytest.raises(TransientRunError):
        review_checks(tmp_path, tmp_path)
    json.dumps(review_checks(tmp_path, tmp_path, status=True))
