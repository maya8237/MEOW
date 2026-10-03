"""Native checkpoint writes the same durable records as CLI runs."""

import json
import subprocess

import pytest

from meow.native.native_state import checkpoint
from meow.execution.run_state import RunStore


def test_native_checkpoint_create_and_interrupted_mutation(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "dev", str(tmp_path)], check=True)
    (tmp_path / ".harness.toml").write_text("", encoding="utf-8")
    plan = tmp_path / "plan.md"
    plan.write_text("plan", encoding="utf-8")

    created = checkpoint(tmp_path, tmp_path, "planning", request="do work")
    run_id = created["run_id"]
    checkpoint(tmp_path, tmp_path, "planned", run_id=run_id, plan_file=plan)
    checkpoint(tmp_path, tmp_path, "generator_started", run_id=run_id, round_num=1)

    record = RunStore(tmp_path).load(run_id)
    assert record.phase == "generator_started"
    assert record.plan_file == str(plan)
    assert record.round == 1
    assert record.usage == "unavailable"
    assert json.loads((tmp_path / ".meow" / "runs" / f"{run_id}.json").read_text())


def test_native_checkpoint_cli_returns_run_id(tmp_path):
    from tests.native.test_native_cli import run_native

    subprocess.run(["git", "init", "-q", "-b", "dev", str(tmp_path)], check=True)
    code, output, error = run_native(
        "checkpoint",
        "planning",
        "--working-dir",
        str(tmp_path),
        "--request",
        "do work",
    )
    assert code == 0, error
    run_id = json.loads(output)["run_id"]
    assert RunStore(tmp_path).load(run_id).phase == "planning"


def test_native_checkpoint_cannot_claim_completion_without_gates(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "dev", str(tmp_path)], check=True)
    created = checkpoint(tmp_path, tmp_path, "planning", request="do work")
    with pytest.raises(ValueError, match="completion"):
        checkpoint(tmp_path, tmp_path, "complete", run_id=created["run_id"])
