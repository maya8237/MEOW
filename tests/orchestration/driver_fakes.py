"""A scripted world for the CLI driver tests: scripted judgement agents and a
simulation that finishes child runs whenever the driver sleeps."""

import asyncio
import json
from types import SimpleNamespace

from meow.execution.orchestration import pull_request as pull_request_module
from meow.execution.orchestration.intake import IntakeRequest
from meow.execution.orchestration.runner import resume_orchestrator, start_orchestrator
from meow.execution.orchestration.store import OrchestratorStore
from tests.orchestration.fakes import (
    ScriptedLauncher,
    fail_child,
    finish_child,
    git,
    make_repository,
)
from tests.orchestration.review_fakes import ReviewScript, ScriptedPullRequest

MAX_SLEEPS = 200
PR_URL = "https://example.test/pr/1"
GITHUB_CONFIG = '[github.mcp]\ncommand = "x"\n'
OBJECTIVE = IntakeRequest("objective", objective="Build it")


class DriverKilled(BaseException):
    """Raised from inside the driver to simulate the process dying: it passes
    through every `except Exception` and leaves the journal as it was."""


def task_dict(task_id, request=None, depends_on=()):
    return {
        "id": task_id,
        "request": request or f"Build {task_id}",
        "depends_on": list(depends_on),
    }


class AgentScript:
    """The orchestrator's judgement agents, scripted.

    `tasks` is what the decomposer writes, `decisions` the triage decisions in
    order (a dict is written as JSON, a string verbatim), `resolutions` the
    content the integrator writes for each conflicted path."""

    def __init__(
        self,
        tasks,
        *,
        decisions=(),
        resolutions=None,
        review_statuses=("PASS",),
    ):
        self.tasks = tasks
        self.decisions = list(decisions)
        self.resolutions = resolutions or {}
        self.review = ReviewScript(review_statuses)
        self.pull_request = ScriptedPullRequest(url=PR_URL)
        self.decomposer_calls = []
        self.triage_calls = []
        self.integrator_calls = []
        self.integrator_error = None
        self.decomposer_error = None
        self.agents = SimpleNamespace(
            decomposer=lambda context: _Decomposer(self),
            triage=lambda context: _Triage(self),
            integrator=lambda context: _Integrator(self, context),
            reviewer=self.review.agents.reviewer,
            fixer=self.review.agents.fixer,
            tester=self.review.agents.tester,
            pull_request=self.pull_request.factory,
            issue_fetcher=None,
        )


class _Decomposer:
    def __init__(self, script):
        self.script = script

    async def run(self, objective, output_file, *, task_drafts=None, correction=None):
        self.script.decomposer_calls.append(objective)
        if self.script.decomposer_error is not None:
            raise self.script.decomposer_error
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(
            json.dumps({"tasks": self.script.tasks}), encoding="utf-8"
        )
        return output_file


class _Triage:
    def __init__(self, script):
        self.script = script

    async def run(  # ruff: ignore[too-many-arguments] -- mirrors the real signature
        self, task_request, cause, evidence, output_file, *, cycles, max_cycles
    ):
        script = self.script
        script.triage_calls.append({
            "request": task_request,
            "cause": cause,
            "evidence": list(evidence),
            "cycles": cycles,
        })
        assert script.decisions, f"unexpected triage call for cause {cause}"
        decision = script.decisions.pop(0)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(
            decision if isinstance(decision, str) else json.dumps(decision),
            encoding="utf-8",
        )
        return output_file


class _Integrator:
    def __init__(self, script, context):
        self.script = script
        self.worktree = context.active_working_dir()

    async def resolve(self, conflicted_paths, task_request, merged_requests):
        script = self.script
        script.integrator_calls.append(list(conflicted_paths))
        if script.integrator_error is not None:
            raise script.integrator_error
        for path in conflicted_paths:
            (self.worktree / path).write_text(
                script.resolutions[path], encoding="utf-8"
            )


class Simulation:
    """Finishes child runs when the driver sleeps, following a script.

    `outcomes[task_id]` lists what each attempt does: `("pass", {file: text})`,
    `("fail", reason)`, or `("hang",)` (stays running). `before[(task_id, n)]`
    runs just before attempt n of a task finishes. `kill_after` raises
    `DriverKilled` on that many-th sleep."""

    def __init__(self, repo, outcomes, *, launcher=None, kill_after=None):
        self.repo = repo
        self.outcomes = outcomes
        self.launcher = launcher or ScriptedLauncher()
        self.kill_after = kill_after
        self.before = {}
        self.settled = set()
        self.sleeps = 0

    async def sleep(self, seconds):
        self.sleeps += 1
        assert self.sleeps < MAX_SLEEPS, "the driver never settled"
        if self.kill_after is not None and self.sleeps >= self.kill_after:
            raise DriverKilled
        for launch in list(self.launcher.launches):
            if launch["run_id"] not in self.settled:
                self._advance(launch)

    def _advance(self, launch):
        task_id, run_id = launch["task_id"], launch["run_id"]
        attempt = self.launcher.run_ids(task_id).index(run_id)
        outcome = self.outcomes[task_id][attempt]
        if outcome[0] == "hang":
            return
        hook = self.before.get((task_id, attempt))
        if hook is not None:
            hook()
        self.settled.add(run_id)
        if outcome[0] == "pass":
            finish_child(self.repo, run_id, outcome[1])
        else:
            fail_child(self.repo, run_id, outcome[1])


class World:
    """A repository, a bare remote, scripted agents and simulated children."""

    def __init__(self, tmp_path, monkeypatch, script, outcomes, **simulation):
        self.repo, self.remote = make_repository(tmp_path, GITHUB_CONFIG)
        self.script = script
        self.sim = Simulation(self.repo, outcomes, **simulation)
        self.store = OrchestratorStore(self.repo)
        monkeypatch.setattr(
            pull_request_module, "origin_url", lambda repo: "git@github.com:o/r.git"
        )

    def start(self, **options):
        return asyncio.run(
            start_orchestrator(
                self.repo,
                options.pop("intake", OBJECTIVE),
                agents=self.script.agents,
                launcher=self.sim.launcher,
                sleep=self.sim.sleep,
                **options,
            )
        )

    def resume(self, orch_id, **options):
        return asyncio.run(
            resume_orchestrator(
                self.repo,
                orch_id,
                agents=self.script.agents,
                launcher=self.sim.launcher,
                sleep=self.sim.sleep,
                **options,
            )
        )

    def record(self, orch_id):
        return self.store.load(orch_id)

    def pushed(self):
        return git(self.remote, "for-each-ref")
