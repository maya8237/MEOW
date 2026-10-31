"""What a re-review needs from the previous round, kept on the sprint config.

A reviewer that only sees the plan restarts from scratch every round. After the
first review, the loop remembers the verdict and the reviewed tree; after each
generator turn, its final reply. The next plan review gets all three.
"""

import asyncio
from pathlib import Path

from meow.agents.reviewer import PriorReview
from meow.execution.sprint import Sprint
from meow.infrastructure.review_delta import changes_since, snapshot_tree
from meow.project.plan_files import plan_review_file, plan_test_file

REPLY_KEY = "_workflow_generator_reply"
VERDICT_KEY = "_workflow_prior_verdict"
TREE_KEY = "_workflow_reviewed_tree"


def reset(sprint: Sprint) -> None:
    """Forget the previous loop's rounds; a new loop starts at round 1."""
    for key in (REPLY_KEY, VERDICT_KEY, TREE_KEY):
        sprint.config.pop(key, None)


def note_reply(sprint: Sprint, reply: str) -> None:
    sprint.config[REPLY_KEY] = reply if isinstance(reply, str) else ""


def _reports(plan_file: Path) -> tuple[Path, ...]:
    return plan_review_file(plan_file), plan_test_file(plan_file)


async def prior_for(sprint: Sprint, plan_file: Path) -> PriorReview | None:
    """The previous round's memory, or None before any review has run."""
    verdict = sprint.config.get(VERDICT_KEY)
    if not verdict:
        return None
    delta = await asyncio.to_thread(
        changes_since,
        sprint.active_working_dir(),
        sprint.config.get(TREE_KEY),
        _reports(plan_file),
    )
    return PriorReview(
        verdict,
        sprint.config.get(REPLY_KEY, ""),
        delta.stat,
        delta.diff,
        delta.since,
    )


async def remember(sprint: Sprint, plan_file: Path, verdict: str) -> None:
    """Record this review's verdict and tree; its reply has been consumed."""
    sprint.config[VERDICT_KEY] = verdict
    sprint.config[TREE_KEY] = await asyncio.to_thread(
        snapshot_tree, sprint.active_working_dir(), _reports(plan_file)
    )
    sprint.config.pop(REPLY_KEY, None)
