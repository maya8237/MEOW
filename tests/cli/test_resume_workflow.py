import asyncio
import subprocess
from unittest.mock import AsyncMock, patch

import pytest

from meow.cli.resume_cli import resume
from meow.cli.status_cli import render
from meow.execution.run_state import RunStore
from meow.execution.sprint_runner import run_sprint
from meow.execution.workflow.engine import GateFailed
from meow.project.config import load_config
from meow.project.custom.workflows import fingerprint


def setup_workflow(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "dev", str(tmp_path)], check=True)
    directory = tmp_path / ".meow" / "workflows"
    directory.mkdir(parents=True)
    path = directory / "audit.toml"
    path.write_text(
        'description="Audit"\n[[steps]]\nid="first"\nagent="explorer"\n'
        '[[steps]]\nid="second"\nagent="explorer"\n',
        encoding="utf-8",
    )
    (tmp_path / ".meow" / "config.toml").write_text(
        '[workflow]\ndefault="audit"\n', encoding="utf-8"
    )
    catalog = load_config(tmp_path)["customizations"]
    metadata = {
        "name": "audit",
        "scope": "project",
        "fingerprint": fingerprint(catalog.workflow("audit"), catalog),
        "completed": ["first"],
        "active": "second",
    }
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt", request="Audit", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    return store, record, metadata, path


def test_workflow_journal_and_resume_skip_completed_steps(tmp_path):
    store, record, metadata, _ = setup_workflow(tmp_path)
    store.transition(record.id, "stage_started", results={"workflow": metadata})
    with patch(
        "meow.execution.workflow.stages.StageAgent.run", new=AsyncMock(return_value="")
    ) as run:
        asyncio.run(
            run_sprint(tmp_path, None, "Audit", use_worktree=False, run_id=record.id)
        )
    assert run.await_count == 1
    current = store.load(record.id)
    assert current.results["workflow"]["completed"] == ["first", "second"]
    assert current.results["workflow"]["name"] == "audit"
    assert current.results["workflow"]["scope"] == "project"
    assert current.results["workflow"]["fingerprint"] == metadata["fingerprint"]
    assert current.results["workflow"]["active"] is None


def test_changed_workflow_refuses_resume_before_agent(tmp_path, capsys):
    store, record, metadata, path = setup_workflow(tmp_path)
    store.transition(record.id, "failed", results={"workflow": metadata})
    path.write_text(path.read_text() + "\n# edited\n", encoding="utf-8")
    with patch("meow.cli.resume_cli.run_sprint", new=AsyncMock(return_value="")) as run:
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 1
    run.assert_not_awaited()
    assert "workflow changed" in capsys.readouterr().err.lower()


def test_legacy_record_uses_default_despite_new_config_default(tmp_path):
    store, record, _, _ = setup_workflow(tmp_path)
    plan = tmp_path / "plan.md"
    plan.write_text("Plan", encoding="utf-8")
    store.transition(record.id, "interrupted_mutation", plan_file=str(plan))
    with patch("meow.cli.resume_cli.run_sprint", new=AsyncMock(return_value="")) as run:
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 0
    assert run.await_args.kwargs["workflow"] == "default"
    assert run.await_args.kwargs["resume_at"] == "review"


def test_resume_propagates_saved_unattended_policy(tmp_path):
    store, record, _, _ = setup_workflow(tmp_path)
    store.transition(record.id, "failed", delivery={"unattended": True})
    captured = {}

    fake_run_sprint = AsyncMock(
        side_effect=lambda *args, **kwargs: captured.update(kwargs)
    )
    with patch("meow.cli.resume_cli.run_sprint", new=fake_run_sprint):
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 0

    assert captured["unattended"] is True


@pytest.mark.parametrize("continue_run", [False, True])
def test_original_request_reaches_sprint_on_run_and_resume(tmp_path, continue_run):
    store, record, metadata, _ = setup_workflow(tmp_path)
    store.transition(record.id, "failed", results={"workflow": metadata})
    with patch("meow.execution.sprint_runner.run_workflow", new=AsyncMock()) as execute:
        if continue_run:
            assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 0
        else:
            asyncio.run(run_sprint(tmp_path, None, record.request, use_worktree=False))
    state = execute.await_args.args[0]
    assert state.request == record.request
    assert state.sprint.original_request == record.request


def test_interrupted_custom_loop_resumes_review_first_without_plan(tmp_path):
    store, record, metadata, path = setup_workflow(tmp_path)
    path.write_text(
        'description="Audit"\n[[steps]]\nid="rounds"\n'
        'loop=[{id="gate", gate="reviewer"}]\n',
        encoding="utf-8",
    )
    catalog = load_config(tmp_path)["customizations"]
    metadata.update(
        fingerprint=fingerprint(catalog.workflow("audit"), catalog),
        completed=[],
        active="rounds",
    )
    store.transition(record.id, "stage_started", results={"workflow": metadata})
    with patch("meow.cli.resume_cli.run_sprint", new=AsyncMock(return_value="")) as run:
        assert asyncio.run(resume(tmp_path, record.id, continue_run=True)) == 0
    assert run.await_args.kwargs["resume_at"] == "review"
    assert run.await_args.kwargs["workflow"] == "audit"


def test_status_renders_workflow_and_custom_stages(tmp_path):
    store, record, metadata, _ = setup_workflow(tmp_path)
    store.transition(
        record.id,
        "stage_finished",
        results={"workflow": metadata, "stages": {"audit": {"status": "PASS"}}},
    )
    output = render(store.load(record.id))
    assert "Workflow: audit (project)" in output
    assert "Stage audit: PASS" in output


def test_metadata_update_does_not_duplicate_phases(tmp_path):
    store, record, _, _ = setup_workflow(tmp_path)
    store.update_results(record.id, workflow={"name": "audit"})
    assert [item["phase"] for item in store.load(record.id).transitions] == ["created"]


def test_fresh_run_journals_resolved_workflow(tmp_path):
    _, _, metadata, _ = setup_workflow(tmp_path)
    with patch(
        "meow.execution.workflow.stages.StageAgent.run",
        new=AsyncMock(return_value="Report"),
    ):
        asyncio.run(run_sprint(tmp_path, None, "Audit", use_worktree=False))
    saved = RunStore(tmp_path).latest().results["workflow"]
    assert saved["name"] == "audit"
    assert saved["scope"] == "project"
    assert saved["fingerprint"] == metadata["fingerprint"]
    assert saved["completed"] == ["first", "second"]


def test_resume_preserves_completed_delivery_gate_failure(tmp_path):
    store, record, metadata, path = setup_workflow(tmp_path)
    path.write_text(
        'description="Audit"\n[[steps]]\nid="first"\n'
        'gate="reviewer"\n[[steps]]\nuses="builtin.deliver"\n',
        encoding="utf-8",
    )
    catalog = load_config(tmp_path)["customizations"]
    metadata["fingerprint"] = fingerprint(catalog.workflow("audit"), catalog)
    store.transition(
        record.id,
        "failed",
        results={
            "workflow": metadata,
            "stages": {
                "first": {
                    "status": "FAIL",
                    "summary": "Fix auth",
                    "file": None,
                    "round": 0,
                }
            },
        },
    )
    with (
        patch("meow.execution.workflow.builtin_steps.deliver_verified_run") as deliver,
        patch(
            "meow.execution.workflow.stages.StageAgent.run",
            new=AsyncMock(return_value=""),
        ) as gate,
        pytest.raises(GateFailed),
    ):
        asyncio.run(
            run_sprint(tmp_path, None, "Audit", use_worktree=False, run_id=record.id)
        )
    deliver.assert_not_called()
    gate.assert_not_awaited()
