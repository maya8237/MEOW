"""Existing MEOW operations exposed as composable workflow steps."""

import json
from dataclasses import asdict, replace

from meow.agents.fixers import ReviewFixAgent
from meow.agents.generator import Generator
from meow.agents.planner import PlannerAgent, require_plan_file
from meow.agents.reviewer import ReviewerAgent
from meow.execution.delivery import deliver_verified_run
from meow.execution.orchestrator import (
    PlanNotApprovedError,
    _fix_instruction,
    _generate,
    _journal,
    _review_round,
)
from meow.infrastructure.checks import (
    completion_ready,
    configured_checks,
    run_final_checks,
)
from meow.infrastructure.logging import get_logger
from meow.project.config import config_paths
from meow.project.plan_files import planned_plan_file
from meow.project.plan_state import PlanStore
from meow.project.preplan import gather_context, prepare_preplan
from meow.project.shaping import ShapeContext
from meow.tasks.model import load_task_graph
from meow.tasks.runner import run_parallel_plan

logger = get_logger(__name__)


async def knowledge(state):  # ruff: ignore[unused-async] -- common async step interface
    state.journal("knowledge")
    state.evidence = gather_context(state.sprint.active_working_dir(), state.request)


async def shape(state):  # ruff: ignore[unused-async] -- mirrors existing shaping phase sequence
    state.journal("shaping")
    preplan = prepare_preplan(
        state.sprint.active_working_dir(),
        state.request,
        unattended=state.variables()["unattended"],
        evidence=state.evidence,
    )
    state.journal("shaping_finished", results={"preplan": preplan.to_dict()})
    if preplan.decision.mode == "needs_user_decision":
        state.journal(
            "needs_user_decision",
            last_failure="Unresolved product decision before planning",
        )
        raise RuntimeError(
            "Unresolved product decision before planning: "
            f"{'; '.join(preplan.decision.reasons)}. "
            f'meow resume {state.run_id} --continue --request "<intended outcome>" '
            f'--work-dir "{state.store.load(state.run_id).repo}"'
        )
    if preplan.breadboard is not None:
        state.journal("breadboarding")
        state.journal("breadboard_finished")
    artifact = state.store.directory / f"{state.run_id}.preplan.json"
    artifact.write_text(
        json.dumps(preplan.to_dict(), indent=2) + "\n", encoding="utf-8"
    )
    state.sprint.config["_preplan_context"] = preplan.to_dict()
    if preplan.shape is not None:
        state.sprint.config["_shape_context"] = ShapeContext(
            str(artifact), preplan.shape.chosen_approach, preplan.shape.assumptions
        )


async def plan(state):
    from meow.execution.sprint_runner import _owner_finished

    active = state.sprint.active_working_dir()
    PlanStore(active).claim(
        planned_plan_file(active / state.sprint.config["docs_dir"], state.feature_name),
        state.run_id,
        owner_finished=lambda owner: _owner_finished(state.store, owner),
    )
    state.journal("planning")
    logger.info(
        "planner_started", feature_name=state.feature_name, working_dir=str(active)
    )
    state.plan_file = require_plan_file(
        await PlannerAgent(state.sprint).run(
            state.feature_name, state.request, state.sprint.config.get("_shape_context")
        )
    )
    logger.info("planner_finished", plan_file=str(state.plan_file))
    if state.plan_ready:
        state.plan_ready(state.plan_file)


async def approve_plan(state):  # ruff: ignore[unused-async]
    if state.plan_file is None:
        return
    if state.approve_plan is not None and not state.approve_plan(state.plan_file):
        state.journal("failed", last_failure="Plan not approved")
        raise PlanNotApprovedError(
            f"Plan {state.plan_file} was not approved -- stopping before "
            "the generator runs."
        )
    PlanStore(state.sprint.active_working_dir()).transition(
        state.plan_file, "in-progress", state.run_id
    )


async def tasks(state):
    graph = load_task_graph(state.plan_file) if state.plan_file else None
    if graph is not None and len(graph.tasks) > 1:
        await run_parallel_plan(
            state.sprint, graph, state.plan_file, state.store, state.run_id
        )
        state.flags["parallel"] = True


async def _generator(state):
    agent_name = "generator" if state.plan_file else "review-fixer"
    definition = state.sprint.config["customizations"].agent(agent_name)

    def factory():
        return (
            Generator(state.sprint, state.plan_file)
            if state.plan_file
            else ReviewFixAgent(state.sprint)
        )

    if state.pool is not None and definition.session == "persistent":
        return await state.pool.open(agent_name, factory)
    return factory()


async def generate(state):
    generator = await _generator(state)
    instruction = (
        _fix_instruction(state.feedback, test=state.test)
        if state.feedback
        else f"Implement the tasks in {state.plan_file}."
    )
    if state.plan_file:

        async def operation():
            await _generate(generator, state.sprint, instruction, state.round or 1)

    else:

        async def operation():
            _journal(state.sprint, "generator_started", round=state.round or 1)
            try:
                await generator.fix(state.feedback or state.request)
            except BaseException:
                _journal(state.sprint, "interrupted_mutation", round=state.round or 1)
                raise
            _journal(state.sprint, "generator_finished", round=state.round or 1)

    if state.pool is not None and generator in state.pool.sessions.values():
        await operation()
    else:
        async with generator:
            await operation()


async def review(state, step):
    if state.plan_file:
        status, verdict = await _review_round(
            state.sprint, state.plan_file, state.round or 1, test=state.test
        )
    else:
        if state.test:
            raise ValueError(
                "The exploratory tester requires a plan; use --plan with review-only"
            )
        _journal(state.sprint, "reviewer_started", round=state.round or 1)
        status, verdict = await ReviewerAgent(state.sprint).review_prompt(state.request)
        _journal(
            state.sprint,
            "reviewer_finished",
            round=state.round or 1,
            results={"reviewer": status},
        )
    state.gate_result(
        replace(step, blocks="loop" if state.pool else "delivery"), status, verdict
    )
    return status


def evidence_ready(state):
    verdicts = state.store.load(state.run_id).results
    return completion_ready(
        state.checks_results,
        configured_checks(state.sprint.config),
        state.sprint.active_working_dir(),
        verdicts.get("reviewer", "FAIL") if state.has_review else "PASS",
        verdicts.get("tester", "FAIL") if state.test and state.has_review else None,
        reviewer_revision=verdicts.get("reviewer_revision", "")
        if state.has_review
        else None,
    )


async def checks(state):
    state.block_delivery()
    if not config_paths(state.checks_dir):
        return
    state.journal("checking")
    state.checks_results = await run_final_checks(
        state.sprint.active_working_dir(), state.sprint.config
    )
    state.journal(
        "checks_finished",
        results={"checks": [asdict(item) for item in state.checks_results]},
    )
    if not evidence_ready(state):
        raise RuntimeError(
            "Required checks, review, or tester evidence failed; inspect meow status"
        )


async def deliver(state):  # ruff: ignore[unused-async]
    state.block_delivery()
    if state.checks_results is None:
        return
    if not evidence_ready(state):
        raise RuntimeError("Required evidence failed or became stale")
    if state.store.load(state.run_id).phase != "checks_finished":
        state.journal("checks_finished")
    requested = state.store.load(state.run_id).delivery.get("unattended", False)
    deliver_verified_run(state.store, state.run_id, unattended=requested)


async def run_builtin(state, step):
    if step.target == "builtin.review":
        return await review(state, step)
    functions = {
        "knowledge": knowledge,
        "shape": shape,
        "plan": plan,
        "approve_plan": approve_plan,
        "tasks": tasks,
        "generate": generate,
        "checks": checks,
        "deliver": deliver,
    }
    return await functions[step.target.removeprefix("builtin.")](state)
