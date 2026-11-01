import asyncio
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

from meow.execution.run_state import RunStore
from meow.execution.sprint import Sprint
from meow.execution.workflow import stages
from meow.execution.workflow.engine import RunState
from meow.project.config import load_config


def _state(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    config = load_config(tmp_path)
    config["customizations"] = SimpleNamespace(
        agent=lambda _name: SimpleNamespace(file=tmp_path / "check.toml")
    )
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt", request="check", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    return RunState(
        sprint=Sprint(tmp_path, config, None, None),
        store=store,
        run_id=record.id,
        plan_file=None,
        request="check",
    )


def test_gate_verdict_written_to_a_stray_path_is_adopted(tmp_path):
    state = _state(tmp_path)
    step = SimpleNamespace(
        id="check", kind="gate", target="check", task=None, blocks="delivery"
    )
    stray = tmp_path / "plans" / "check.stage.md"
    stray.parent.mkdir()
    stray.write_text("SUMMARY: stale\nSTATUS: FAIL\n", encoding="utf-8")

    def write_stray(*_args):
        assert not stray.exists()  # the untracked leftover was cleared
        stray.write_text("SUMMARY: works\nSTATUS: PASS\n", encoding="utf-8")

    with patch.object(stages.StageAgent, "run", side_effect=write_stray):
        asyncio.run(stages.run_stage(state, step))
    record = state.store.load(state.run_id)
    assert record.results["stages"]["check"]["status"] == "PASS"
    assert record.results["stray_verdict"] == {"stage": "check", "from": str(stray)}
    assert not stray.exists()
