"""Native helpers under a managing session: exit codes, parking, heartbeats."""

import asyncio
import json
import subprocess
import time
from unittest.mock import patch

from meow.execution.run_state import RunStore, reconcile_stale_runs
from meow.native.native_cli import EXIT_TEMPFAIL, RETRY_AFTER
from meow.native.native_state import checkpoint
from tests.native.test_native_cli import run_native

FRESH = 60  # seconds: a heartbeat stamped by this test


def _repo(tmp_path, config=""):
    subprocess.run(["git", "init", "-q", "-b", "dev", str(tmp_path)], check=True)
    (tmp_path / ".meow").mkdir(exist_ok=True)
    (tmp_path / ".meow" / "config.toml").write_text(config, encoding="utf-8")
    return tmp_path


def _error(stderr):
    return json.loads(stderr.strip().splitlines()[-1])


def test_native_helper_transient_exit_75(tmp_path):
    repo = _repo(tmp_path)
    with patch("meow.native.native.prepare", side_effect=OSError(None, "", None, 32)):
        code, out, err = run_native("prepare", "--work-dir", str(repo))
    assert code == EXIT_TEMPFAIL
    assert out == ""
    payload = _error(err)
    assert payload["retryable"] is True
    assert payload["retry_after"] == RETRY_AFTER
    assert payload["command"][:3] == ["meow", "native", "prepare"]
    assert "prepare" in payload["error"]


def test_native_helper_unrecognised_exception_is_blocking(tmp_path):
    repo = _repo(tmp_path)
    with patch("meow.native.native.prepare", side_effect=KeyError("lost")):
        code, _out, err = run_native("prepare", "--work-dir", str(repo))
    assert code == 1
    payload = _error(err)
    assert payload["retryable"] is False
    assert payload["retry_after"] is None


def test_native_helper_timeout_is_retryable(tmp_path):
    repo = _repo(tmp_path)
    with patch("meow.native.native.prepare", side_effect=TimeoutError("slow")):
        code, _out, err = run_native("prepare", "--work-dir", str(repo))
    assert code == EXIT_TEMPFAIL
    assert _error(err)["retryable"] is True


def test_native_helper_blocking_exit_1(tmp_path):
    repo = _repo(tmp_path)
    code, _out, err = run_native("checkpoint", "complete", "--work-dir", str(repo))
    assert code == 1
    payload = _error(err)
    assert payload["retryable"] is False
    assert payload["cause"] == "invalid_input"
    assert "gate evidence" in payload["error"]


def test_native_checkpoint_parked(tmp_path):
    repo = _repo(tmp_path)
    run_id = checkpoint(repo, repo, "generator_started", request="x")["run_id"]
    code, out, err = run_native(
        "checkpoint",
        "parked",
        "--run-id",
        run_id,
        "--failure",
        "claude session crashed",
        "--work-dir",
        str(repo),
    )
    assert code == 0, err
    assert json.loads(out)["phase"] == "parked"
    record = RunStore(repo).load(run_id)
    assert record.last_failure == "claude session crashed"
    assert record.park["phase_before"] == "generator_started"
    assert record.park["cause"] == "native_reported"
    assert "--repair" in record.results["next_command"]


def test_native_checkpoint_parked_requires_failure(tmp_path):
    repo = _repo(tmp_path)
    run_id = checkpoint(repo, repo, "generator_started", request="x")["run_id"]
    code, _out, err = run_native(
        "checkpoint", "parked", "--run-id", run_id, "--work-dir", str(repo)
    )
    assert code == 1
    assert "--failure" in _error(err)["error"]


def test_checkpoint_intent_and_heartbeat(tmp_path):
    repo = _repo(tmp_path)
    code, out, _err = run_native(
        "checkpoint", "planning", "--intent", "plan", "--work-dir", str(repo)
    )
    assert code == 0
    record = RunStore(repo).load(json.loads(out)["run_id"])
    assert record.results["intent"] == "plan"
    assert time.time() - record.results["heartbeat_at"] < FRESH


def test_helper_with_run_id_stamps_heartbeat(tmp_path):
    repo = _repo(tmp_path)
    store = RunStore(repo)
    run_id = checkpoint(repo, repo, "generator_started", request="x")["run_id"]
    store.update_results(run_id, heartbeat_at=1.0)
    code, out, err = run_native("finalize", run_id, "--work-dir", str(repo))
    assert code == 0, err
    assert json.loads(out)["complete"] is False  # no review yet
    assert store.load(run_id).results["heartbeat_at"] > 1.0


def test_heartbeat_does_not_overwrite_concurrent_transition(tmp_path, monkeypatch):
    """The heartbeat rides on the checkpoint's own write: no separate
    read-modify-write that a concurrent transition could lose to."""
    repo = _repo(tmp_path)
    store = RunStore(repo)
    run_id = checkpoint(repo, repo, "generator_started", request="x")["run_id"]
    writes = []
    real_write = RunStore._write

    def counting(self, record):
        writes.append(record.phase)
        return real_write(self, record)

    monkeypatch.setattr(RunStore, "_write", counting)
    code, _out, err = run_native(
        "checkpoint", "reviewer_started", "--run-id", run_id, "--work-dir", str(repo)
    )
    assert code == 0, err
    assert writes == ["reviewer_started"]
    record = store.load(run_id)
    assert record.phase == "reviewer_started"
    assert time.time() - record.results["heartbeat_at"] < FRESH


def test_stale_run_checkpointing_again_is_not_parked_first(tmp_path):
    repo = _repo(tmp_path, "[run]\nstale_after = 100\n")
    store, run_id = _native_run(repo, intent="run", age=500)
    code, _out, err = run_native(
        "checkpoint", "reviewer_started", "--run-id", run_id, "--work-dir", str(repo)
    )
    assert code == 0, err
    phases = [item["phase"] for item in store.load(run_id).transitions]
    assert "parked" not in phases


def _native_run(repo, *, intent, age, phase="generator_started"):
    run_id = checkpoint(repo, repo, phase, request="x", intent=intent)["run_id"]
    store = RunStore(repo)
    if age is None:
        record = store.load(run_id)
        record.results.pop("heartbeat_at", None)
        store._write(record)
    else:
        store.update_results(run_id, heartbeat_at=time.time() - age)
    return store, run_id


def test_stale_native_run_is_parked(tmp_path):
    repo = _repo(tmp_path, "[run]\nstale_after = 100\n")
    store, run_id = _native_run(repo, intent="run", age=500)
    assert reconcile_stale_runs(store) == [run_id]
    record = store.load(run_id)
    assert record.phase == "parked"
    assert record.park["cause"] == "native_session_lost"
    assert "--repair" in record.results["next_command"]


def test_fresh_plan_and_legacy_native_runs_are_left_alone(tmp_path):
    repo = _repo(tmp_path, "[run]\nstale_after = 100\n")
    fresh = _native_run(repo, intent="run", age=10)[1]
    plan = _native_run(repo, intent="plan", age=500, phase="planned")[1]
    store, legacy = _native_run(repo, intent=None, age=None)
    assert reconcile_stale_runs(store) == []
    assert {store.load(i).phase for i in (fresh, legacy)} == {"generator_started"}
    assert store.load(plan).phase == "planned"


def test_stale_native_run_past_budget_parks_and_reports_stop(tmp_path, capsys):
    from meow.cli.run_contract import record_exit

    repo = _repo(tmp_path, "[run]\nstale_after = 100\nmax_total_resumes = 1\n")
    store, run_id = _native_run(repo, intent="run", age=500)
    store.transition(run_id, "resuming")
    store.transition(run_id, "generator_started")
    store.update_results(run_id, heartbeat_at=time.time() - 500)
    assert reconcile_stale_runs(store) == [run_id]
    record = store.load(run_id)
    assert record.phase == "parked"
    assert record.park["cause"] == "native_session_lost"
    assert record.park["total_resumes"] == 1
    assert "budget_exhausted" not in json.dumps(record.results)
    assert record_exit(record) == 1
    blocked = capsys.readouterr().err.split("MEOW_BLOCKED ", 1)[1]
    assert json.loads(blocked)["stop"] == "budget_exhausted"


def test_parked_plan_intent_run_is_never_auto_resumed(tmp_path):
    from meow.cli.resume_cli import budget_refusal
    from meow.project.config import run_config

    repo = _repo(tmp_path)
    store, run_id = _native_run(repo, intent="plan", age=10, phase="planning")
    record = store.park(run_id, "crash", "transient", "native_reported")
    assert budget_refusal(record, run_config(repo)) == "plan-only native run"
    assert record.results.get("next_command") is None


def test_finalize_that_parks_its_run_exits_75_with_next_command(tmp_path):
    repo = _repo(tmp_path)
    store = RunStore(repo)
    run_id = checkpoint(repo, repo, "generator_started", request="x")["run_id"]

    async def parking_finalize(_wd, _active, rid):
        await asyncio.sleep(0)
        store.park(rid, "git push failed: early EOF", "transient", "error")
        raise RuntimeError("git push failed: early EOF")

    with patch("meow.native.native.finalize", parking_finalize):
        code, _out, err = run_native("finalize", run_id, "--work-dir", str(repo))
    assert code == EXIT_TEMPFAIL, err
    payload = _error(err)
    assert payload["retryable"] is True
    assert payload["next_command"] == store.load(run_id).results["next_command"]
    assert "--repair" in payload["next_command"]


def test_already_parked_run_failing_deterministically_exits_1(tmp_path):
    repo = _repo(tmp_path)
    run_id = checkpoint(repo, repo, "generator_started", request="x")["run_id"]
    RunStore(repo).park(run_id, "earlier crash", "transient", "error")
    code, _out, err = run_native(
        "checkpoint", "complete", "--run-id", run_id, "--work-dir", str(repo)
    )
    assert code == 1, err
    payload = _error(err)
    assert payload["retryable"] is False
    assert payload["next_command"] is None
