"""The prompt-builder call matrix behind `tests/fixtures/prompts_golden.json`.

The golden file recorded every builder's output before role prompts moved
into builtin agent files; `test_prompts_golden.py` replays these calls and
compares, so the move cannot change what a role is told.
"""

from itertools import product
from pathlib import Path

from meow.project.config_models import LintCommand
from meow.project.shaping import ShapeContext

PLAN = Path("/repo/.meow/plans/feature.md")
REVIEW = Path("/repo/.meow/plans/feature-review.md")
SHAPE = ShapeContext("docs/shape.json", "bounded adapter", ("legacy API remains",))
LINT = [LintCommand("ruff check"), LintCommand("fallow", gate=False)]
EVIDENCE = {"files": ["src/a.py"], "notes": "x"}
INTEGRATION_WORKTREE = Path("/repo/.worktrees/orch-0123456789ab")
CONFLICTS = ["src/a.py", "docs/b.md"]
TASKS_FILE = Path("/repo/.meow/orchestrators/0123456789ab/tasks.json")
TASK_DRAFTS = "Task PROJ-1 (PROJ-1):\nResolve Jira issue PROJ-1: Fix login\n\nSteps"


def cases() -> list[tuple[str, str, tuple, dict]]:
    """(case id, builder name, args, kwargs) for every recorded call."""
    found: list[tuple[str, str, tuple, dict]] = [
        ("docs_update", "docs_update_prompt", ("abc123", "def456"), {}),
        ("tester", "tester_prompt", (REVIEW,), {}),
        ("explorer", "explorer_prompt", (Path("/repo"),), {}),
        ("generator", "generator_prompt", (PLAN,), {}),
        ("review_fixer", "review_fixer_prompt", (), {}),
        ("lint_fixer", "lint_fixer_prompt", (), {}),
        ("ci_review", "ci_review_prompt", ("s1", "t2", "m3"), {}),
        ("decomposer-objective", "decomposer_prompt", (TASKS_FILE,), {"max_tasks": 32}),
        (
            "decomposer-jira",
            "decomposer_prompt",
            (TASKS_FILE,),
            {"max_tasks": 32, "task_drafts": TASK_DRAFTS},
        ),
        ("integrator", "integrator_prompt", (INTEGRATION_WORKTREE, CONFLICTS), {}),
    ]
    for shape, evidence, bug in product((None, SHAPE), (None, EVIDENCE), (False, True)):
        found.append((
            f"planner-{bool(shape)}-{bool(evidence)}-{bug}",
            "planner_prompt",
            (PLAN, shape, evidence),
            {"bug_mode": bug},
        ))
    for lint, focus, hygiene, shape in product(
        ([], LINT), (None, "error handling"), (False, True), (None, SHAPE)
    ):
        found.append((
            f"plan_review-{bool(lint)}-{bool(focus)}-{hygiene}-{bool(shape)}",
            "plan_review_prompt",
            (PLAN, REVIEW, lint),
            {"focus": focus, "check_worktree_hygiene": hygiene, "shape_context": shape},
        ))
    for basis, has_diff, hygiene, shape in product(
        ("", "add csv export"), (False, True), (False, True), (None, SHAPE)
    ):
        found.append((
            f"prompt_review-{bool(basis)}-{has_diff}-{hygiene}-{bool(shape)}",
            "prompt_review_prompt",
            (basis, REVIEW, LINT),
            {
                "has_diff": has_diff,
                "docs_dir": ".meow/plans",
                "check_worktree_hygiene": hygiene,
                "shape_context": shape,
            },
        ))
    for label, hygiene in product(
        ("GitLab merge request", "GitHub pull request"), (False, True)
    ):
        found.append((
            f"remote_review-{label.split()[0]}-{hygiene}",
            "remote_review_prompt",
            (REVIEW, label),
            {"check_worktree_hygiene": hygiene},
        ))
    for lint, hygiene, shape in product(([], LINT), (False, True), (None, SHAPE)):
        found.append((
            f"branch_review-{bool(lint)}-{hygiene}-{bool(shape)}",
            "branch_review_prompt",
            ("main", "feature/x", REVIEW, lint),
            {"check_worktree_hygiene": hygiene, "shape_context": shape},
        ))
    return found
