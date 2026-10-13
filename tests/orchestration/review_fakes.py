"""Scripted reviewer, fixer, tester and pull request agents for the objective
review and delivery tests."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

from meow.infrastructure.test_runner import VerificationStageEvidence


class ReviewScript:
    """Scripted reviewer, fixer and tester that record how they were called.

    `statuses[n]` is the verdict of review round n + 1; the last status repeats
    for any later round."""

    def __init__(self, statuses, tester_result=("PASS", "all good")):
        self.statuses = list(statuses)
        self.tester_result = tester_result
        self.reviews = []
        self.fixes = []
        self.tests = []
        self.agents = SimpleNamespace(
            reviewer=lambda context: _Reviewer(self, context),
            fixer=lambda context: _Fixer(self, context),
            tester=lambda context: _Tester(self),
        )


class _Reviewer:
    last_review_file = None

    def __init__(self, script, context):
        self.script = script
        self.worktree = context.active_working_dir()

    async def review_branch(self, target, branch, *, objective=None):
        script = self.script
        script.reviews.append((target, branch, objective))
        number = len(script.reviews)
        status = script.statuses[min(number, len(script.statuses)) - 1]
        verdict = f"SUMMARY: round {number}\nSTATUS: {status}\n"
        path = self.worktree / ".meow" / "plans" / f"branch.0000000{number}.review.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(verdict, encoding="utf-8")
        self.last_review_file = path
        return status, verdict


class _Fixer:
    def __init__(self, script, context):
        self.script = script
        self.worktree = context.active_working_dir()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def fix(self, problems):
        self.script.fixes.append(problems)
        (self.worktree / "fix.txt").write_text(
            f"fix {len(self.script.fixes)}\n", encoding="utf-8"
        )
        return "fixed"


class _Tester:
    def __init__(self, script):
        self.script = script

    async def test_request(self, request, evidence):
        self.script.tests.append(request)
        return self.script.tester_result


@asynccontextmanager
async def fake_stage(worktree, config):
    yield VerificationStageEvidence()


class ScriptedPullRequest:
    """Stands in for `PullRequestAgent`: `factory` is `agents.pull_request`."""

    def __init__(self, url=None, error=None):
        self.url = url
        self.error = error
        self.calls = []
        self.built = []

    def factory(self, context, mcp, provider):
        self.built.append((context.active_working_dir(), mcp, provider))
        return self

    async def open(self, *call):
        self.calls.append(call)
        if self.error:
            raise RuntimeError(self.error)
        return self.url
