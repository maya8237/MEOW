"""Durable run journal contract."""

import json

import pytest

from meow.execution.run_state import MAX_OUTPUT, RunStateError, RunStore


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
