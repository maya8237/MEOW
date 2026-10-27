"""Durable run journal contract."""

import json
import os

import pytest

from meow.execution.run_state import MAX_OUTPUT, RunStateError, RunStore


@pytest.mark.parametrize(
    "delivery",
    [
        {},
        {"enabled": True, "push": True},
        {"enabled": True, "push": False},
        {"push": False},
        {"enabled": False, "push": False},
    ],
)
def test_delivery_fields_round_trip(tmp_path, delivery):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt", request="x", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    path = store.directory / f"{record.id}.json"
    raw = json.loads(path.read_text())
    raw["delivery"] = delivery
    path.write_text(json.dumps(raw))
    before = path.read_bytes()

    assert store.load(record.id).delivery == delivery
    assert path.read_bytes() == before
    assert store.update_results(record.id, resumed=True).delivery == delivery
    assert json.loads(path.read_text())["delivery"] == delivery


def test_create_transition_and_latest(tmp_path):
    store = RunStore(tmp_path)
    first = store.create(
        source="prompt",
        request="build it",
        repo=tmp_path,
        worktree=tmp_path,
        branch="dev",
    )
    assert first.id and first.phase == "created"
    assert first.usage == "unavailable"
    moved = store.transition(first.id, "planning", attempt=1, round=0)
    assert moved.id == first.id
    assert moved.transitions[-1]["phase"] == "planning"
    assert store.load(first.id).phase == "planning"
    second = store.create(
        source="jira", request="PROJ-1", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    assert store.latest().id == second.id
    assert not list(store.directory.glob("*.tmp"))


def test_run_records_retain_schema_and_role_session_references(tmp_path):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt", request="x", repo=tmp_path, worktree=tmp_path, branch="dev"
    )

    assert record.schema_version == 1
    assert record.sessions == {}
    saved = (store.directory / f"{record.id}.json").read_text(encoding="utf-8")
    assert saved.endswith("\n")
    assert store.set_session(record.id, "planner", "claude-session-1").sessions == {
        "planner": "claude-session-1"
    }


def test_old_run_records_load_with_empty_sessions(tmp_path):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt", request="x", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    path = store.directory / f"{record.id}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.pop("schema_version")
    raw.pop("sessions")
    path.write_text(json.dumps(raw), encoding="utf-8")

    assert store.load(record.id).sessions == {}


def test_future_run_record_schema_is_rejected(tmp_path):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt", request="x", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    path = store.directory / f"{record.id}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["schema_version"] = 999
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(RunStateError, match="corrupt"):
        store.load(record.id)


def test_missing_and_corrupt_records_are_safe(tmp_path):
    store = RunStore(tmp_path)
    with pytest.raises(RunStateError, match="meow status"):
        store.load("unknown")
    record = store.create(
        source="prompt", request="x", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    (store.directory / f"{record.id}.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(RunStateError, match="corrupt"):
        store.load(record.id)


def test_valid_json_with_invalid_field_types_is_corrupt(tmp_path):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt", request="x", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    path = store.directory / f"{record.id}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["created_at"] = {"bad": "date"}
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(RunStateError, match="corrupt"):
        store.load(record.id)


def test_state_read_redacts_bearer_and_rejects_bad_transitions(tmp_path):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt", request="x", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    path = store.directory / f"{record.id}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["results"] = {"output": "Authorization: Bearer topsecret"}
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert "topsecret" not in str(store.load(record.id).results)
    raw["transitions"] = [{}]
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(RunStateError, match="corrupt"):
        store.load(record.id)


def test_redacts_secrets_and_bounds_output(tmp_path):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt",
        request="token=abc123",
        repo=tmp_path,
        worktree=tmp_path,
        branch="dev",
    )
    store.transition(
        record.id,
        "checking",
        results={
            "lint": {
                "output": "x" * 10000,
                "api_token": "abc123",
                "usage": None,
            }
        },
    )
    raw = (store.directory / f"{record.id}.json").read_text(encoding="utf-8")
    assert "abc123" not in raw
    assert len(json.loads(raw)["results"]["lint"]["output"]) <= MAX_OUTPUT
    assert json.loads(raw)["results"]["lint"]["usage"] == "unavailable"


def test_latest_survives_a_record_deleted_while_listing(tmp_path, monkeypatch):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt", request="x", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    ghost = store.directory / "20200101-000000-deadbeef.json"
    real = store.record_paths()
    monkeypatch.setattr(store, "record_paths", lambda: [ghost, *real])
    assert store.latest().id == record.id


def test_stored_check_results_tolerate_schema_drift():
    from meow.execution.run_state import CheckResult

    item = {
        "kind": "lint",
        "command": "ruff check .",
        "required": True,
        "exit_code": 0,
        "duration": 1.0,
        "output": "",
        "revision": "r",
        "config_fingerprint": "f",
    }
    assert CheckResult.from_record({**item, "added_later": 1}).passed
    assert CheckResult.from_record({"kind": "lint"}) is None
    assert CheckResult.from_record("garbage") is None


DEFAULT_ATTEMPTS = 10


def _denied(winerror=5):
    error = PermissionError(13, "denied")
    error.winerror = winerror
    return error


def _fake_replace(monkeypatch, failures):
    import os

    real = os.replace
    calls = {"n": 0}

    def replace(source, target):
        calls["n"] += 1
        if failures is None or calls["n"] <= failures:
            raise _denied()
        return real(source, target)

    monkeypatch.setattr("meow.common.atomic.os.replace", replace)
    sleeps = []
    monkeypatch.setattr("meow.common.atomic.time.sleep", sleeps.append)
    return calls, sleeps


def test_replace_retries_winerror_5(tmp_path, monkeypatch):
    from meow.common.atomic import replace_with_retry

    _, sleeps = _fake_replace(monkeypatch, 2)
    source, target = tmp_path / "a.tmp", tmp_path / "a"
    source.write_text("x")
    replace_with_retry(source, target)
    assert target.read_text() == "x"
    assert sleeps == [0.05, 0.1]


def test_replace_gives_up(tmp_path, monkeypatch):
    calls, sleeps = _fake_replace(monkeypatch, None)
    store = RunStore(tmp_path)
    with pytest.raises(PermissionError):
        store.create(
            source="prompt", request="x", repo=tmp_path, worktree=tmp_path, branch="d"
        )
    assert calls["n"] == DEFAULT_ATTEMPTS
    assert len(sleeps) == DEFAULT_ATTEMPTS - 1
    assert list(store.directory.glob("*.tmp")) == []


def test_replace_does_not_retry_other_errors(tmp_path, monkeypatch):
    from meow.common.atomic import replace_with_retry

    calls, sleeps = _fake_replace(monkeypatch, 0)
    with pytest.raises(FileNotFoundError):
        replace_with_retry(tmp_path / "missing", tmp_path / "target")
    assert calls["n"] == 1
    assert sleeps == []


@pytest.mark.parametrize(("claudecode", "host"), [("1", "claude-code"), (None, "cli")])
def test_run_records_driver(tmp_path, monkeypatch, claudecode, host):
    from meow.common.process import process_identity

    if claudecode is None:
        monkeypatch.delenv("CLAUDECODE", raising=False)
    else:
        monkeypatch.setenv("CLAUDECODE", claudecode)
    store = RunStore(tmp_path)
    run = store.create(
        source="prompt", request="r", repo=tmp_path, worktree=tmp_path, branch="b"
    )
    driver = store.set_driver(run.id).driver
    assert driver == {
        "pid": os.getpid(),
        "start_identity": process_identity(os.getpid()),
        "host": host,
    }
    assert store.load(run.id).driver == driver
