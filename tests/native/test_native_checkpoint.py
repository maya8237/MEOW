"""Native checkpoint writes the same durable records as CLI runs."""

import json
import subprocess
from unittest.mock import patch

import pytest

from meow.execution.run_state import RunStore
from meow.infrastructure.checks import config_fingerprint
from meow.native.native_state import checkpoint


def test_native_checkpoint_create_and_interrupted_mutation(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "dev", str(tmp_path)], check=True)
    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow" / "config.toml").write_text("", encoding="utf-8")
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


def test_native_checkpoint_fingerprints_repository_config_for_worktree(tmp_path):
    repo = tmp_path / "repo"
    active = tmp_path / "worktree"
    repo.mkdir()
    active.mkdir()
    (repo / ".meow").mkdir()
    (repo / ".meow" / "config.toml").write_text("max_rounds = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "dev", str(repo)], check=True)

    with patch("meow.project.config.user_config_path", return_value=tmp_path / "none"):
        created = checkpoint(repo, active, "planning", request="do work")
        record = RunStore(repo).load(created["run_id"])
        expected = config_fingerprint(repo)

    assert record.config_fingerprint == expected
