import asyncio
from unittest.mock import patch

from structlog.testing import capture_logs

from meow.execution.workflow.engine import run_workflow
from meow.execution.workflow.hot_reload import haiku_verdict_roles
from meow.project.custom.workflows import Step
from tests.execution.test_workflow_stall import gate_loop, state_for


def write_models(tmp_path, text):
    config = tmp_path / ".meow" / "config.toml"
    config.parent.mkdir(exist_ok=True)
    config.write_text(text, encoding="utf-8")


def rewriting_gate(tmp_path, after_first):
    """Stage double recording the reviewer model per round; rewrites config once."""
    models = []

    async def run(agent, task, values, pool):  # ruff: ignore[unused-async] -- SDK double
        models.append(state.sprint.model("reviewer"))
        if len(models) == 1:
            write_models(tmp_path, after_first)
        verdict = "PASS" if len(models) == len(["one", "two"]) else "FAIL"
        values["output_file"].write_text(
            f"SUMMARY: x\nSTATUS: {verdict}\n- FAIL round {len(models)}",
            encoding="utf-8",
        )

    write_models(tmp_path, '[models]\nreviewer = "sonnet"\n')
    state = state_for(tmp_path)
    return (
        state,
        models,
        patch("meow.execution.workflow.stages.StageAgent.run", side_effect=run),
    )


def test_model_change_on_disk_applies_in_the_next_round(tmp_path):
    state, models, stage = rewriting_gate(tmp_path, '[models]\nreviewer = "opus"\n')
    with stage:
        asyncio.run(run_workflow(state, (gate_loop(),)))
    assert models == ["sonnet", "opus"]
    reloaded = state.store.load(state.run_id).results["config_reloaded"]
    assert reloaded["round"] == len(models)
    assert reloaded["models"] == ["reviewer"]


def test_invalid_config_on_disk_keeps_the_old_models(tmp_path):
    state, models, stage = rewriting_gate(tmp_path, "[models\nreviewer = ")
    with stage, capture_logs() as logs:
        asyncio.run(run_workflow(state, (gate_loop(),)))
    assert models == ["sonnet", "sonnet"]
    assert any(log["event"] == "config_reload_failed" for log in logs)
    assert "config_reloaded" not in state.store.load(state.run_id).results


def test_haiku_tester_warns_at_start(tmp_path):
    write_models(tmp_path, '[models]\ntester = "haiku"\n')
    state = state_for(tmp_path)
    assert haiku_verdict_roles(state.sprint, (gate_loop(),)) == {"tester": "haiku"}
    with capture_logs() as logs:
        asyncio.run(run_workflow(state, (Step("sequence", "noop", body=()),)))
    warned = [log for log in logs if log["event"] == "haiku_verdict_role"]
    assert [(log["role"], log["source"]) for log in warned] == [("tester", "start")]


def test_reload_to_haiku_reviewer_warns(tmp_path):
    state, models, stage = rewriting_gate(tmp_path, '[models]\nreviewer = "haiku"\n')
    with stage, capture_logs() as logs:
        asyncio.run(run_workflow(state, (gate_loop(),)))
    assert models == ["sonnet", "haiku"]
    warned = [log for log in logs if log["event"] == "haiku_verdict_role"]
    assert [(log["role"], log["source"]) for log in warned] == [("reviewer", "reload")]


def test_agent_definition_edits_are_not_hot_reloaded(tmp_path):
    seen = []

    async def run(agent, task, values, pool):  # ruff: ignore[unused-async] -- SDK double
        catalog = state.sprint.config["customizations"]
        seen.append(catalog.agent("reviewer").tools)
        if len(seen) == 1:  # an agent widening its own tools mid-run
            agents = tmp_path / ".meow" / "agents"
            agents.mkdir(parents=True)
            (agents / "reviewer.toml").write_text(
                'description = "Widened"\ntools = ["Bash"]\nprompt = "x"\n',
                encoding="utf-8",
            )
        verdict = "PASS" if len(seen) > 1 else "FAIL"
        values["output_file"].write_text(
            f"SUMMARY: x\nSTATUS: {verdict}\n- FAIL", encoding="utf-8"
        )

    state = state_for(tmp_path)
    stage = patch("meow.execution.workflow.stages.StageAgent.run", side_effect=run)
    with stage, capture_logs() as logs:
        asyncio.run(run_workflow(state, (gate_loop(),)))
    assert seen[0] == seen[1]
    assert "WebFetch" not in (seen[1] or ())
    edits = [
        log for log in logs if log["event"] == "agent_definitions_changed_not_applied"
    ]
    assert edits and edits[0]["agents"] == ["reviewer"]
