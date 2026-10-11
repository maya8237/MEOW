"""Execute validated workflow trees with gates and scoped loop sessions."""

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path

from meow.agents.base import SessionAgent
from meow.execution.run_state import RunStore
from meow.execution.sprint import Sprint
from meow.infrastructure.cancellation import cancellable, check_cancel
from meow.infrastructure.usage import usage_scope
from meow.project.custom.expressions import evaluate
from meow.project.custom.rendering import prompt_context
from meow.project.custom.workflows import Step, walk
from meow.project.plan_files import plan_review_file, plan_test_file


class LoopExhausted(RuntimeError):
    """No round passed before its limit."""


class GateFailed(RuntimeError):
    def __init__(self, step_id: str, blocks: str):
        self.step_id, self.blocks = step_id, blocks
        super().__init__(f"Gate {step_id!r} failed (blocks {blocks})")


class SessionPool:
    """One lazily opened session per role, closed with its enclosing loop."""

    def __init__(self):
        self.stack = AsyncExitStack()
        self.sessions = {}
        self.locks = {}

    async def open(self, role, factory):
        if role not in self.sessions:
            self.sessions[role] = await self.stack.enter_async_context(factory())
        return self.sessions[role]

    async def run(self, context, role, options, message):
        async with self.locks.setdefault(role, asyncio.Lock()):

            def factory():
                agent = SessionAgent(context)
                agent.role = role
                agent._open_session(options)
                return agent

            agent = await self.open(role, factory)
            return await agent._turn(message)


@dataclass
class RunState:
    sprint: Sprint
    store: RunStore
    run_id: str
    plan_file: Path | None = None
    flags: dict = field(default_factory=dict)
    approve_plan: object = None
    test: bool = False
    checks_dir: Path | None = None
    feature_name: str | None = None
    request: str = ""
    completed: set[str] = field(default_factory=set)
    plan_ready: object = None
    workflow: dict = field(default_factory=dict)
    statuses: dict = field(default_factory=dict)
    gates: dict = field(default_factory=dict)
    delivery_gates: dict = field(default_factory=dict)
    feedback: str = ""
    round: int = 0
    pool: SessionPool | None = None
    has_review: bool = False
    evidence: object = None
    checks_results: object = None

    def variables(self) -> dict:
        from meow.infrastructure.checks import configured_checks

        return {
            "plan_given": self.plan_file is not None,
            "resume_review": False,
            "parallel": False,
            "test": self.test,
            "bug_mode": bool(self.sprint.config.get("_bug_mode")),
            "unattended": bool(self.sprint.config.get("_unattended")),
            "checks_configured": bool(configured_checks(self.sprint.config)),
            "inputs": {},
            **self.flags,
            "review_first": bool(
                self.flags.get("resume_review") or self.flags.get("parallel")
            ),
            "round": self.round,
            "feedback": self.feedback,
            "steps": self.statuses,
        }

    def journal(self, phase, **patch):
        self.store.transition(self.run_id, phase, **patch)

    def prompt_variables(self):
        return {
            **self.variables(),
            "active_dir": self.sprint.active_working_dir(),
            "docs_dir": self.sprint.config["docs_dir"],
            "plan_file": self.plan_file,
            "request": self.request,
            "feature_name": self.feature_name,
            "check_worktree_hygiene": self.sprint.use_worktree,
            "preplan": self.sprint.config.get("_preplan_context"),
        }

    def block_delivery(self):
        for step_id, status in self.delivery_gates.items():
            if status != "PASS":
                raise GateFailed(step_id, "delivery")

    def gate_result(self, step, status, verdict):
        blocks = step.blocks or "loop"
        self.gates[step.id] = (status, verdict, blocks)
        if blocks == "delivery":
            self.delivery_gates[step.id] = status
        if status != "PASS" and (
            blocks == "run" or (blocks == "loop" and self.pool is None)
        ):
            raise GateFailed(step.id, blocks)


class WorkflowRun:
    def __init__(self, state: RunState):
        self.state = state

    async def run(self, steps):
        self.state.has_review = any(
            item.target == "builtin.review" for item in walk(steps)
        )
        for step in steps:
            if step.id in self.state.completed:
                continue
            if self.state.workflow:
                self.state.workflow["active"] = step.id
                self.save_progress()
            await self.step(step)
            self.state.completed.add(step.id)
            if self.state.workflow:
                self.state.workflow["completed"] = sorted(self.state.completed)
                self.state.workflow["active"] = None
                self.save_progress()
        self.state.block_delivery()

    def save_progress(self):
        self.state.store.update_results(
            self.state.run_id,
            workflow=self.state.workflow,
            step_statuses=self.state.statuses,
        )

    async def body(self, steps):
        for step in steps:
            await self.step(step)

    async def step(self, step):
        state = self.state
        check_cancel(state.store, state.run_id)
        if step.when and not evaluate(step.when, state.variables()):
            state.statuses[step.id] = {"status": "skipped"}
            if step.kind in {"agent", "gate"}:
                from meow.execution.workflow.stages import record_stage

                record_stage(state, step, "skipped", "", None)
            return
        try:
            with (
                usage_scope(state.store, state.run_id),
                prompt_context(state.prompt_variables()),
            ):
                async with asyncio.timeout(step.timeout):
                    await cancellable(state.store, state.run_id, self.execute(step))
        except TimeoutError:
            if step.kind != "gate":
                raise
            from meow.execution.workflow.stages import record_stage

            verdict = "SUMMARY: Stage timed out.\nSTATUS: FAIL"
            record_stage(state, step, "FAIL", verdict, None)
            state.gate_result(step, "FAIL", verdict)
        check_cancel(state.store, state.run_id)

    async def execute(self, step):  # ruff: ignore[complex-structure] -- dispatch one of six validated step kinds
        from meow.execution.workflow import builtin_steps, stages

        if step.kind in {"agent", "gate"}:
            await stages.run_stage(self.state, step)
        elif step.kind == "uses":
            status = await builtin_steps.run_builtin(self.state, step)
            self.state.statuses[step.id] = {"status": status or "done"}
        elif step.kind == "loop":
            await self.run_loop(step)
        elif step.kind == "parallel":
            async with asyncio.TaskGroup() as tasks:
                for branch in step.body:
                    tasks.create_task(self.step(branch))
        elif step.kind == "sequence":
            await self.body(step.body)
        else:
            raise ValueError(f"Unexpanded or unsupported step {step.kind!r}")
        self.state.statuses.setdefault(step.id, {"status": "done"})

    async def run_loop(self, step):  # ruff: ignore[too-many-statements] -- loop state and session restoration are paired in finally
        state = self.state
        previous = state.pool, state.round, state.gates
        state.pool = SessionPool()
        old_pool = state.sprint.config.get("_workflow_pool")
        state.sprint.config["_workflow_pool"] = state.pool
        if state.workflow:
            state.workflow["loop"] = step.id
            self.save_progress()
        try:
            for number in range(
                1, (step.max_rounds or state.sprint.config["max_rounds"]) + 1
            ):
                state.round, state.gates = number, {}
                await self.body(step.body)
                failed = [
                    verdict
                    for status, verdict, blocks in state.gates.values()
                    if status != "PASS" and blocks != "advise"
                ]
                if not failed:
                    state.statuses[step.id] = {"status": "PASS"}
                    if state.workflow:
                        state.workflow["loop"] = None
                        self.save_progress()
                    return
                state.feedback = "\n\n".join(failed)
            raise LoopExhausted(self.exhausted_message(step))
        finally:
            await state.pool.stack.aclose()
            state.pool, state.round, state.gates = previous
            if old_pool is None:
                state.sprint.config.pop("_workflow_pool", None)
            else:
                state.sprint.config["_workflow_pool"] = old_pool

    def exhausted_message(self, step):
        state = self.state
        limit = step.max_rounds or state.sprint.config["max_rounds"]
        label = state.flags.get("label_feature_name", state.feature_name)
        if (
            any(item.target == "builtin.review" for item in walk(step.body))
            and state.plan_file
        ):
            return (
                f"Sprint{f' {label!r}' if label else ''} did not pass "
                f"after {limit} rounds -- stopping instead of looping forever. "
                "Inspect the review "
                f"file {plan_review_file(state.plan_file)}"
                + (
                    f" and tester file {plan_test_file(state.plan_file)}"
                    if state.test
                    else "."
                )
            )
        return f"Workflow loop {step.id!r} did not pass after {limit} rounds."


async def run_workflow(state: RunState, steps: tuple[Step, ...]) -> None:
    await WorkflowRun(state).run(steps)
