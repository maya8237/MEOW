import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from meow.agents import explorer, generator, planner
from meow.agents.base import ProjectContext
from meow.execution.sprint import Sprint


def make_sprint(tmp_path: Path) -> Sprint:
    config = {
        "models": {
            "explorer": "test-model",
            "planner": "test-model",
            "generator": "test-model",
            "reviewer": "test-model",
        },
        "lint": [],
        "docs_dir": "docs/exec-plans/active",
        "max_rounds": 1,
        "lint_timeout": 60,
    }
    return Sprint(
        repo_dir=tmp_path,
        config=config,
        explorer=explorer.make_explorer_agent(ProjectContext(tmp_path, config)),
        lint_hook=None,
        working_dir=tmp_path,
    )


class AgentSkillTests(unittest.TestCase):
    def setUp(self):
        self.tmp_path = Path.cwd()

    def test_explorer_uses_debugging_skill_for_read_only_investigation(self):
        agent = explorer.make_explorer_agent(
            ProjectContext(
                self.tmp_path,
                {"models": {"explorer": "test-model"}, "lint": []},
            )
        )

        self.assertEqual(agent.skills, ["superpowers:systematic-debugging"])
        self.assertIn("task is about a bug", agent.prompt)
        self.assertIn("read-only", agent.prompt)

    def test_planner_uses_writing_plans_with_meow_constraints(self):
        async def empty_query(*args, **kwargs):
            await asyncio.sleep(0)
            if False:
                yield None

        with (
            patch("meow.agents.base.query", side_effect=empty_query) as query,
            patch.object(Path, "mkdir"),
            patch.object(Path, "is_file", return_value=True),
        ):
            result = asyncio.run(
                planner.run_planner(make_sprint(self.tmp_path), "feature", "Request")
            )

        options = query.call_args.kwargs["options"]
        self.assertEqual(result, self.tmp_path / "docs/exec-plans/active/feature.md")
        self.assertEqual(options.skills, ["superpowers:writing-plans"])
        self.assertIsNotNone(options.agents["explorer"])
        self.assertIn("task sizing", options.system_prompt)
        self.assertIn("configured plan path", options.system_prompt)
        self.assertIn("Sprint Contract", options.system_prompt)
        self.assertIn("execution-method handoff", options.system_prompt)

    def test_generator_uses_plan_implementation_and_review_skills(self):
        with patch("meow.agents.base.ClaudeSDKClient") as client:
            generator.Generator(make_sprint(self.tmp_path), self.tmp_path / "plan.md")

        options = client.call_args.kwargs["options"]
        self.assertEqual(
            options.skills,
            [
                "superpowers:executing-plans",
                "superpowers:test-driven-development",
                "superpowers:systematic-debugging",
                "superpowers:receiving-code-review",
                "superpowers:verification-before-completion",
            ],
        )
        self.assertIsNotNone(options.agents["explorer"])
        self.assertIn("PostToolUse", options.hooks)
        self.assertIn("one task at a time", options.system_prompt)
        self.assertIn("orchestrator", options.system_prompt)
        self.assertIn("ledger or commits", options.system_prompt)
        self.assertIn("write a failing test", options.system_prompt)
        self.assertIn("verify each finding", options.system_prompt)
        self.assertIn("actual command results", options.system_prompt)

    def test_both_reviewer_sessions_use_verification_skill_and_verdict_format(self):
        from meow.agents import reviewer

        sprint = make_sprint(self.tmp_path)
        plan_file = self.tmp_path / "plan.md"
        observed_options = []

        with (
            patch.object(
                reviewer.ReviewerAgent,
                "run_query",
                new_callable=AsyncMock,
            ) as run_query,
            patch(
                "meow.agents.reviewer._git_review_context",
                return_value=("context", True),
            ),
            patch.object(Path, "mkdir"),
            patch.object(
                Path,
                "read_text",
                return_value="SUMMARY: checked\nSTATUS: PASS\n",
            ),
        ):
            asyncio.run(reviewer.ReviewerAgent(sprint).review_plan(plan_file))
            observed_options.append(run_query.call_args.args[1])
            asyncio.run(reviewer.ReviewerAgent(sprint).review_prompt("Request"))
            observed_options.append(run_query.call_args.args[1])

        self.assertEqual(len(observed_options), 2)
        for options in observed_options:
            self.assertEqual(
                options.skills, ["superpowers:verification-before-completion"]
            )
            self.assertIn("observed command output", options.system_prompt)
            self.assertIn("do not modify implementation", options.system_prompt)
        self.assertIn(
            str(self.tmp_path / "plan-review.md"),
            observed_options[0].system_prompt,
        )
        self.assertRegex(
            observed_options[1].system_prompt,
            r"exec-plans[\\/]active[\\/]prompt\.[0-9a-f]{8}\.review\.md",
        )
