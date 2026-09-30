"""Loop-mechanics coverage for orchestrator.py's three round-loop shapes.

Existing coverage elsewhere (test_cli.py, test_review_cli.py) exercises each
shape's boundary cases -- an immediate PASS (zero loop iterations) and a
review that never passes (exhausting max_rounds, raised by the caller) --
but always with a constant reviewer verdict. None of it actually drives the
loop body itself: a FAIL, FAIL, ... PASS sequence spanning several rounds,
where round 2's fix depends on round 1 having actually run. These tests
patch the same seams (`Generator`, `ReviewerAgent.review_plan`,
`ReviewFixAgent`) the existing tests do, but with a `side_effect` sequence
instead of a constant return value, and assert the loop actually iterated
the expected number of times before stopping.
"""

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from meow.agents.base import ProjectContext
from meow.orchestrator import (
    _run_prompt_fix_rounds,
    _run_review_rounds,
    _run_rounds,
)
from meow.sprint import Sprint

PLAN_FILE = Path("/project/plan.md")


def _sprint(max_rounds: int) -> Sprint:
    return Sprint(
        repo_dir=Path("/project"),
        config={
            "models": {
                "planner": "x", "generator": "x", "reviewer": "x", "explorer": "x",
            },
            "lint": [],
            "docs_dir": "docs",
            "max_rounds": max_rounds,
        },
        explorer=None,
        lint_hook=None,
    )


class RunRoundsLoopTests(unittest.IsolatedAsyncioTestCase):
    """`_run_rounds`: generator implements every round, from round 1."""

    async def test_loops_through_failing_rounds_before_passing(self):
        with (
            patch("meow.orchestrator.Generator") as mock_generator_cls,
            patch(
                "meow.orchestrator.ReviewerAgent.review_plan",
                new=AsyncMock(
                    side_effect=[
                        ("FAIL", "STATUS: FAIL\nround 1 issue"),
                        ("FAIL", "STATUS: FAIL\nround 2 issue"),
                        ("PASS", "STATUS: PASS\n"),
                    ]
                ),
            ) as mock_review,
        ):
            generator = mock_generator_cls.return_value.__aenter__.return_value
            generator.implement = AsyncMock()

            result = await _run_rounds(_sprint(max_rounds=5), PLAN_FILE)

        self.assertTrue(result)
        self.assertEqual(generator.implement.await_count, 3)
        self.assertEqual(mock_review.await_count, 3)
        # Round 2's instruction must carry round 1's feedback forward.
        second_call_instruction = generator.implement.await_args_list[1].args[0]
        self.assertIn("round 1 issue", second_call_instruction)

    async def test_never_passing_exhausts_max_rounds_and_returns_false(self):
        with (
            patch("meow.orchestrator.Generator") as mock_generator_cls,
            patch(
                "meow.orchestrator.ReviewerAgent.review_plan",
                new=AsyncMock(return_value=("FAIL", "STATUS: FAIL\n")),
            ) as mock_review,
        ):
            generator = mock_generator_cls.return_value.__aenter__.return_value
            generator.implement = AsyncMock()

            result = await _run_rounds(_sprint(max_rounds=3), PLAN_FILE)

        self.assertFalse(result)
        self.assertEqual(generator.implement.await_count, 3)
        self.assertEqual(mock_review.await_count, 3)


class RunReviewRoundsLoopTests(unittest.IsolatedAsyncioTestCase):
    """`_run_review_rounds`: reviews first, only implements from round 2 on."""

    async def test_loops_through_failing_rounds_before_passing(self):
        with (
            patch("meow.orchestrator.Generator") as mock_generator_cls,
            patch(
                "meow.orchestrator.ReviewerAgent.review_plan",
                new=AsyncMock(
                    side_effect=[
                        ("FAIL", "STATUS: FAIL\nround 1 issue"),
                        ("FAIL", "STATUS: FAIL\nround 2 issue"),
                        ("PASS", "STATUS: PASS\n"),
                    ]
                ),
            ) as mock_review,
        ):
            generator = mock_generator_cls.return_value.__aenter__.return_value
            generator.implement = AsyncMock()

            result = await _run_review_rounds(_sprint(max_rounds=5), PLAN_FILE)

        self.assertTrue(result)
        # 3 reviews (round 1's initial review + rounds 2, 3) but only 2
        # generator rounds (round 1 has nothing to implement yet).
        self.assertEqual(mock_review.await_count, 3)
        self.assertEqual(generator.implement.await_count, 2)
        second_call_instruction = generator.implement.await_args_list[1].args[0]
        self.assertIn("round 2 issue", second_call_instruction)

    async def test_never_passing_exhausts_max_rounds_and_returns_false(self):
        with (
            patch("meow.orchestrator.Generator") as mock_generator_cls,
            patch(
                "meow.orchestrator.ReviewerAgent.review_plan",
                new=AsyncMock(return_value=("FAIL", "STATUS: FAIL\n")),
            ) as mock_review,
        ):
            generator = mock_generator_cls.return_value.__aenter__.return_value
            generator.implement = AsyncMock()

            result = await _run_review_rounds(_sprint(max_rounds=3), PLAN_FILE)

        self.assertFalse(result)
        self.assertEqual(mock_review.await_count, 3)
        self.assertEqual(generator.implement.await_count, 2)


class RunPromptFixRoundsLoopTests(unittest.IsolatedAsyncioTestCase):
    """`_run_prompt_fix_rounds`: fixes and re-reviews, from round 2 on."""

    async def test_loops_through_failing_rounds_before_passing(self):
        context = ProjectContext(Path("/project"), _sprint(max_rounds=5).config)
        re_review = AsyncMock(
            side_effect=[
                ("FAIL", "STATUS: FAIL\nround 2 issue"),
                ("PASS", "STATUS: PASS\n"),
            ]
        )
        with patch("meow.orchestrator.ReviewFixAgent") as mock_fixer_cls:
            fixer = mock_fixer_cls.return_value.__aenter__.return_value
            fixer.fix = AsyncMock()

            result = await _run_prompt_fix_rounds(
                context,
                ("FAIL", "STATUS: FAIL\nround 1 issue"),
                re_review=re_review,
            )

        self.assertTrue(result)
        self.assertEqual(fixer.fix.await_count, 2)
        self.assertEqual(re_review.await_count, 2)
        first_fix_verdict = fixer.fix.await_args_list[0].args[0]
        second_fix_verdict = fixer.fix.await_args_list[1].args[0]
        self.assertIn("round 1 issue", first_fix_verdict)
        self.assertIn("round 2 issue", second_fix_verdict)

    async def test_never_passing_exhausts_max_rounds_and_returns_false(self):
        context = ProjectContext(Path("/project"), _sprint(max_rounds=3).config)
        re_review = AsyncMock(return_value=("FAIL", "STATUS: FAIL\n"))
        with patch("meow.orchestrator.ReviewFixAgent") as mock_fixer_cls:
            fixer = mock_fixer_cls.return_value.__aenter__.return_value
            fixer.fix = AsyncMock()

            result = await _run_prompt_fix_rounds(
                context, ("FAIL", "STATUS: FAIL\n"), re_review=re_review
            )

        self.assertFalse(result)
        self.assertEqual(fixer.fix.await_count, 2)
        self.assertEqual(re_review.await_count, 2)
