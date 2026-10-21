import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meow.execution.run_state import RunStore
from meow.execution.sprint import Sprint
from meow.execution.workflow.engine import (
    GateFailed,
    LoopExhausted,
    RunState,
    run_workflow,
)
from meow.project.config import load_config
from meow.project.custom.workflows import Step, expand
from meow.project.plan_files import plan_stage_file, stage_file

EXPECTED_ROUNDS = 2
PARALLEL_BRANCHES = 2


def state_for(tmp_path, *, plan=False):
    config = load_config(tmp_path)
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt",
        request="Improve",
        repo=tmp_path,
        worktree=tmp_path,
        branch="dev",
    )
    config["_run_journal"] = store, record.id
    sprint = Sprint(tmp_path, config, None, None)
    plan_file = tmp_path / "plan.md" if plan else None
    if plan:
        plan_file.write_text("Plan", encoding="utf-8")
    return RunState(
        sprint=sprint,
        store=store,
        run_id=record.id,
        plan_file=plan_file,
        checks_dir=tmp_path,
        feature_name="feature",
        request="Improve",
    )


def stages(state):
    return state.store.load(state.run_id).results["stages"]


def test_custom_stage_records_artifact_and_skipped_condition(tmp_path):
    state = state_for(tmp_path)
    steps = (
        Step("agent", "audit", target="explorer", task="Explore {{inputs.topic}}"),
        Step("agent", "off", when="test", target="explorer"),
    )
    state.flags["inputs"] = {"topic": "API"}

    async def run(agent, task, values, pool):  # ruff: ignore[unused-async] -- SDK double
        assert task == "Explore API"
        assert values["output_file"] == stage_file(
            tmp_path / state.sprint.config["docs_dir"], "audit"
        )
        values["output_file"].write_text("Observed API", encoding="utf-8")

    with patch("meow.execution.workflow.stages.StageAgent.run", side_effect=run):
        asyncio.run(run_workflow(state, steps))
    assert stages(state)["audit"]["status"] == "done"
    assert stages(state)["audit"]["summary"] == "Observed API"
    assert stages(state)["off"]["status"] == "skipped"
    phases = [entry["phase"] for entry in state.store.load(state.run_id).transitions]
    assert phases == ["created", "stage_started", "stage_finished", "stage_finished"]


def test_gate_fail_feeds_next_round_and_success_ends_loop(tmp_path):
    state = state_for(tmp_path, plan=True)
    steps = (
        Step(
            "loop",
            "rounds",
            max_rounds=3,
            body=(
                Step(
                    "gate",
                    "security",
                    target="reviewer",
                    blocks="loop",
                    task="{{feedback}}",
                ),
            ),
        ),
    )
    tasks = []

    async def run(agent, task, values, pool):  # ruff: ignore[unused-async]
        tasks.append(task)
        values["output_file"].write_text(
            "SUMMARY: fix auth\nSTATUS: " + ("FAIL" if len(tasks) == 1 else "PASS"),
            encoding="utf-8",
        )

    with patch("meow.execution.workflow.stages.StageAgent.run", side_effect=run):
        asyncio.run(run_workflow(state, steps))
    assert tasks[0] == ""
    assert "fix auth" in tasks[1]
    assert len(tasks) == EXPECTED_ROUNDS
    assert stages(state)["security"]["status"] == "PASS"
    assert stages(state)["security"]["round"] == EXPECTED_ROUNDS
    assert stages(state)["security"]["file"] == str(
        plan_stage_file(state.plan_file, "security")
    )


@pytest.mark.parametrize("blocks", ["run", "delivery"])
def test_failed_gate_prevents_delivery(tmp_path, blocks):
    state = state_for(tmp_path)
    steps = (
        Step("gate", "security", target="reviewer", blocks=blocks),
        Step("uses", "deliver", target="builtin.deliver"),
    )
    with (
        patch("meow.execution.workflow.stages.StageAgent.run", new=AsyncMock()),
        patch("meow.execution.workflow.builtin_steps.deliver_verified_run") as deliver,
        pytest.raises(GateFailed),
    ):
        asyncio.run(run_workflow(state, steps))
    deliver.assert_not_called()
    assert stages(state)["security"]["status"] == "FAIL"


def test_timed_out_gate_fails_closed(tmp_path):
    state = state_for(tmp_path)

    async def slow(*args):
        await asyncio.Event().wait()

    with (
        patch("meow.execution.workflow.stages.StageAgent.run", side_effect=slow),
        pytest.raises(GateFailed),
    ):
        asyncio.run(
            run_workflow(
                state,
                (
                    Step(
                        "gate",
                        "timeout",
                        timeout=0.01,
                        target="reviewer",
                        blocks="delivery",
                    ),
                ),
            )
        )
    assert stages(state)["timeout"]["status"] == "FAIL"


def test_parallel_waits_for_both_branches_and_runs_concurrently(tmp_path):
    state = state_for(tmp_path)
    entered = set()

    async def run(agent, task, values, pool):
        entered.add(task)
        while len(entered) < PARALLEL_BRANCHES:
            await asyncio.sleep(0)
        values["output_file"].write_text(task, encoding="utf-8")

    steps = (
        Step(
            "parallel",
            "audits",
            timeout=1,
            body=(
                Step("agent", "a", target="explorer", task="first"),
                Step("agent", "b", target="explorer", task="second"),
            ),
        ),
    )
    with patch("meow.execution.workflow.stages.StageAgent.run", side_effect=run):
        asyncio.run(run_workflow(state, steps))
    assert stages(state)["a"]["status"] == "done"
    assert stages(state)["b"]["status"] == "done"


def test_workflow_without_review_checks_without_reviewer_verdict(tmp_path):
    state = state_for(tmp_path)
    (tmp_path / ".meow" / "config.toml").write_text("", encoding="utf-8")
    steps = (Step("uses", "checks", target="builtin.checks"),)
    asyncio.run(run_workflow(state, steps))
    assert state.store.load(state.run_id).phase == "checks_finished"
    assert "reviewer" not in state.store.load(state.run_id).results


def test_agent_prompt_receives_workflow_flags_and_stdout_is_retained(tmp_path):
    from claude_agent_sdk import AssistantMessage, TextBlock

    directory = tmp_path / ".meow" / "agents"
    directory.mkdir(parents=True)
    (directory / "reporter.toml").write_text(
        'description="Report"\ntools=["Read"]\n'
        'prompt="{{#if test}}Test{{/if}} {{inputs.topic}}"',
        encoding="utf-8",
    )
    state = state_for(tmp_path)
    state.test = True
    state.flags.update(inputs={"topic": "API"})

    async def query(*, prompt, options):  # ruff: ignore[unused-async] -- SDK async iterator double
        assert options.system_prompt == "Test API"
        yield AssistantMessage(
            content=[TextBlock(text="Found API boundaries")], model="x"
        )

    with patch("meow.agents.base.query", side_effect=query):
        asyncio.run(run_workflow(state, (Step("agent", "report", target="reporter"),)))
    assert stages(state)["report"]["summary"] == "Found API boundaries"


@pytest.mark.parametrize("review_first", [False, True])
def test_default_rounds_preserve_phase_sequence(tmp_path, review_first):
    state = state_for(tmp_path, plan=True)
    state.flags.update(plan_given=True, resume_review=review_first)
    workflow = state.sprint.config["customizations"].workflow("default")
    # Plan is already known; inspect the round loop directly.
    steps = tuple(
        step
        for step in expand(workflow, state.sprint.config["customizations"])
        if step.kind == "loop"
    )
    generator = AsyncMock()
    generator.__aenter__.return_value = generator
    with (
        patch(
            "meow.execution.workflow.builtin_steps.Generator", return_value=generator
        ),
        patch(
            "meow.execution.orchestrator.ReviewerAgent.review_plan",
            new=AsyncMock(return_value=("PASS", "SUMMARY: good\nSTATUS: PASS")),
        ),
    ):
        asyncio.run(run_workflow(state, steps))
    phases = [item["phase"] for item in state.store.load(state.run_id).transitions][1:]
    expected = ["reviewer_started", "reviewer_finished"]
    if not review_first:
        expected = ["generator_started", "generator_finished", *expected]
    assert phases == expected


def test_builtin_review_exhaustion_preserves_message(tmp_path):
    state = state_for(tmp_path, plan=True)
    state.sprint.config["max_rounds"] = 1
    step = Step(
        "loop", "rounds", body=(Step("uses", "review", target="builtin.review"),)
    )
    with (
        patch(
            "meow.execution.orchestrator.ReviewerAgent.review_plan",
            new=AsyncMock(return_value=("FAIL", "STATUS: FAIL")),
        ),
        pytest.raises(
            LoopExhausted, match="Sprint 'feature' did not pass after 1 rounds"
        ),
    ):
        asyncio.run(run_workflow(state, (step,)))


def test_persistent_stage_shares_session_until_loop_finishes(tmp_path):  # ruff: ignore[too-many-statements] -- session lifecycle integration
    directory = tmp_path / ".meow" / "agents"
    directory.mkdir(parents=True)
    (directory / "audit.toml").write_text(
        'description="Audit"\ntools=["Read","Write"]\nsession="persistent"\n'
        'prompt="Audit {{request}}"',
        encoding="utf-8",
    )
    state = state_for(tmp_path)
    step = Step(
        "loop",
        "rounds",
        max_rounds=3,
        body=(Step("gate", "audit", target="audit", blocks="loop"),),
    )
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock()
    client.query = AsyncMock()
    rounds = []

    async def response():  # ruff: ignore[unused-async] -- SDK async iterator double
        rounds.append(1)
        stage_file(tmp_path / state.sprint.config["docs_dir"], "audit").write_text(
            "STATUS: " + ("FAIL" if len(rounds) == 1 else "PASS"), encoding="utf-8"
        )
        if False:
            yield

    client.receive_response.side_effect = lambda: response()
    with patch("meow.agents.base.ClaudeSDKClient", return_value=client) as sdk:
        asyncio.run(run_workflow(state, (step,)))
    sdk.assert_called_once()
    client.__aexit__.assert_awaited_once()
    assert client.query.await_count == EXPECTED_ROUNDS


def test_persistent_builtin_reviewer_uses_enclosing_loop_session(tmp_path):  # ruff: ignore[too-many-statements] -- session lifecycle integration
    from meow.project.plan_files import plan_review_file

    directory = tmp_path / ".meow" / "agents"
    directory.mkdir(parents=True)
    (directory / "reviewer.toml").write_text(
        'description="Review"\ntools=["Read","Write"]\nsession="persistent"\n'
        'prompt="Round {{round}}. Review {{plan_file}}. {{verdict_format}}"',
        encoding="utf-8",
    )
    state = state_for(tmp_path, plan=True)
    step = Step(
        "loop",
        "rounds",
        max_rounds=3,
        body=(Step("uses", "review", target="builtin.review"),),
    )
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock()
    client.query = AsyncMock()
    rounds = []

    async def response():  # ruff: ignore[unused-async] -- SDK async iterator double
        rounds.append(1)
        plan_review_file(state.plan_file).write_text(
            "STATUS: " + ("FAIL" if len(rounds) == 1 else "PASS"), encoding="utf-8"
        )
        if False:
            yield

    client.receive_response.side_effect = lambda: response()
    with (
        patch("meow.agents.base.ClaudeSDKClient", return_value=client) as sdk,
        patch(
            "meow.agents.base.query",
            side_effect=AssertionError("persistent needs a client"),
        ),
    ):
        asyncio.run(run_workflow(state, (step,)))
    sdk.assert_called_once()
    client.__aexit__.assert_awaited_once()
    assert client.query.await_count == EXPECTED_ROUNDS
    assert "Round 2." in client.query.await_args_list[1].args[0]


@pytest.mark.parametrize("role,plan", [("generator", True), ("review-fixer", False)])
def test_persistent_mutator_renders_current_round(tmp_path, role, plan):
    directory = tmp_path / ".meow" / "agents"
    directory.mkdir(parents=True)
    (directory / f"{role}.toml").write_text(
        'description="Implement"\ntools=["Read"]\nsession="persistent"\n'
        'prompt="Round {{round}}."',
        encoding="utf-8",
    )
    state = state_for(tmp_path, plan=plan)
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock()
    client.query = AsyncMock()

    async def response():  # ruff: ignore[unused-async] -- async iterator double
        if False:
            yield

    client.receive_response.side_effect = lambda: response()
    method = "review_plan" if plan else "review_prompt"
    step = Step(
        "loop",
        "rounds",
        max_rounds=2,
        body=(
            Step("uses", "generate", target="builtin.generate"),
            Step("uses", "review", target="builtin.review"),
        ),
    )
    with (
        patch("meow.agents.base.ClaudeSDKClient", return_value=client),
        patch(
            f"meow.agents.reviewer.ReviewerAgent.{method}",
            new=AsyncMock(side_effect=[("FAIL", "Fix"), ("PASS", "OK")]),
        ),
    ):
        asyncio.run(run_workflow(state, (step,)))
    assert "Round 2." in client.query.await_args_list[1].args[0]


@pytest.mark.parametrize("mode", ["fresh", "plan", "resume", "parallel", "test"])
def test_default_workflow_full_phase_order(tmp_path, mode):  # ruff: ignore[too-many-statements, complex-structure] -- exercise the five documented run shapes
    from meow.execution.sprint_runner import run_sprint
    from meow.project.plan_files import planned_plan_file

    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow" / "config.toml").write_text("", encoding="utf-8")
    config = load_config(tmp_path)
    sprint = Sprint(tmp_path, config, None, None)
    plan = planned_plan_file(tmp_path / config["docs_dir"], "feature")
    plan.parent.mkdir(parents=True)
    plan.write_text("Plan", encoding="utf-8")
    if mode == "parallel":
        plan.with_suffix(".md.tasks.json").write_text(
            '{"tasks":[{"id":"a","depends_on":[],"owned_paths":["a"],"verification":[]},'
            '{"id":"b","depends_on":[],"owned_paths":["b"],"verification":[]}]}',
            encoding="utf-8",
        )
    generator = MagicMock()
    generator.__aenter__ = AsyncMock(return_value=generator)
    generator.__aexit__ = AsyncMock()
    generator.implement = AsyncMock()
    kwargs = {}
    if mode in {"plan", "resume", "parallel"}:
        kwargs["plan_file"] = plan
    if mode == "resume":
        kwargs["resume_at"] = "review"
    if mode == "test":
        kwargs["test"] = True
    with (
        patch(
            "meow.execution.sprint_runner._prepare_sprint",
            return_value=(sprint, "feature", tmp_path),
        ),
        patch(
            "meow.execution.workflow.builtin_steps.PlannerAgent.run",
            new=AsyncMock(return_value=plan),
        ),
        patch(
            "meow.execution.workflow.builtin_steps.Generator", return_value=generator
        ),
        patch(
            "meow.execution.orchestrator.ReviewerAgent.review_plan",
            new=AsyncMock(return_value=("PASS", "STATUS: PASS")),
        ),
        patch(
            "meow.execution.orchestrator.VerificationAgent.test_plan",
            new=AsyncMock(return_value=("PASS", "STATUS: PASS")),
        ),
        patch(
            "meow.execution.workflow.builtin_steps.run_parallel_plan", new=AsyncMock()
        ),
        patch("meow.execution.workflow.builtin_steps.deliver_verified_run"),
    ):
        asyncio.run(
            run_sprint(
                tmp_path, "feature", "Improve the CLI", use_worktree=False, **kwargs
            )
        )
    phases = [entry["phase"] for entry in RunStore(tmp_path).latest().transitions]
    expected = ["created", "preparing", "prepared", "onboarded"]
    if mode in {"fresh", "test"}:
        expected += ["knowledge", "shaping", "shaping_finished", "planning"]
    expected += ["planned"]
    if mode not in {"resume", "parallel"}:
        expected += ["generator_started", "generator_finished"]
    expected += ["reviewer_started", "reviewer_finished"]
    if mode == "test":
        expected += ["tester_finished"]
    expected += ["checking", "checks_finished", "complete"]
    assert phases == expected
