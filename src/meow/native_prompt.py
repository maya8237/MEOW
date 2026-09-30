"""
meow/native_prompt.py

Prompt/agent-wiring construction for native (in-Claude-Code-session)
execution -- the `role_prompt` helper `native.py` re-exports for
`native_cli.py` to wire to `meow native prompt`. Split out on its own
because building the exact system prompt (and task message, where there is
one) a Task subagent needs to behave like an SDK role is a distinct concern
from directory bootstrapping (`native_prepare.py`), lint execution
(`native_lint.py`), or round-counter persistence (`native_state.py`).
"""

import re
from pathlib import Path

from meow.agents.base import ProjectContext
from meow.agents.reviewer import (
    MR_REVIEW_FILENAME,
    PROMPT_REVIEW_FILENAME,
    _git_review_context,
)
from meow.config import load_config
from meow.prompts import (
    explorer_prompt,
    generator_prompt,
    lint_fixer_prompt,
    mr_review_prompt,
    plan_review_prompt,
    planner_prompt,
    prompt_review_prompt,
    review_fixer_prompt,
)
from meow.rules import load_rules

PROMPT_ROLES = (
    "planner",
    "generator",
    "explorer",
    "reviewer-plan",
    "reviewer-prompt",
    "reviewer-mr",
    "review-fixer",
    "lint-fixer",
)


def _with_rules(text: str, active_dir: Path, role: str) -> str:
    return text + (load_rules(active_dir, role) or "")


def _plan_review(
    context: ProjectContext, plan_file: Path, focus: str | None
) -> dict:
    review_file = plan_file.with_name(plan_file.stem + "-review.md")
    text = plan_review_prompt(
        plan_file,
        review_file,
        context.lint_commands(),
        focus=focus,
        check_worktree_hygiene=context.use_worktree,
    )
    return {
        "system_prompt": text,
        "query": f"Review {plan_file}",
        "review_file": str(review_file),
    }


def _prompt_review(context: ProjectContext, basis: str | None) -> dict:
    docs_dir = context.config["docs_dir"]
    review_file = context.active_working_dir() / docs_dir / PROMPT_REVIEW_FILENAME
    git_context, has_diff = _git_review_context(context)
    text = prompt_review_prompt(
        (basis or "").strip(),
        review_file,
        context.lint_commands(),
        has_diff=has_diff,
        docs_dir=docs_dir,
        check_worktree_hygiene=context.use_worktree,
    )
    query = (
        f"Review the prompt: {basis.strip()}\n\n{git_context}"
        if basis and basis.strip()
        else f"Review the working tree.\n\n{git_context}"
    )
    return {"system_prompt": text, "query": query, "review_file": str(review_file)}


def _mr_review(context: ProjectContext) -> dict:
    review_file = (
        context.active_working_dir() / context.config["docs_dir"] / MR_REVIEW_FILENAME
    )
    text = mr_review_prompt(review_file, check_worktree_hygiene=context.use_worktree)
    return {"system_prompt": text, "query": None, "review_file": str(review_file)}


def _simple_prompt(role: str, context: ProjectContext, plan_file: Path | None) -> dict:
    active_dir = context.active_working_dir()
    if role in {"planner", "generator"} and plan_file is None:
        raise ValueError(f"the {role} prompt needs --plan")
    builders = {
        "planner": lambda: planner_prompt(plan_file),
        "generator": lambda: generator_prompt(plan_file),
        "explorer": lambda: explorer_prompt(active_dir),
        "review-fixer": review_fixer_prompt,
        "lint-fixer": lint_fixer_prompt,
    }
    return {"system_prompt": builders[role](), "query": None}


def role_prompt(  # ruff: ignore[too-many-arguments] -- mirrors the `meow native prompt` flags one to one
    working_dir: Path,
    active_dir: Path,
    role: str,
    *,
    plan_file: Path | None = None,
    focus: str | None = None,
    use_worktree: bool = False,
) -> dict:
    """The exact system prompt (and task message, where there is one) the SDK
    agent for `role` would use, including project rules, so a Task subagent
    launched with it behaves like the SDK role."""
    if role not in PROMPT_ROLES:
        raise ValueError(f"role must be one of {PROMPT_ROLES}, got {role!r}")
    config = load_config(working_dir)
    context = ProjectContext(active_dir, config)
    context.use_worktree = use_worktree
    if role == "reviewer-plan":
        if plan_file is None:
            raise ValueError("the reviewer-plan prompt needs --plan")
        result = _plan_review(context, plan_file, focus)
    elif role == "reviewer-prompt":
        result = _prompt_review(context, focus)
    elif role == "reviewer-mr":
        result = _mr_review(context)
    else:
        result = _simple_prompt(role, context, plan_file)
    rules_key = re.sub(r"-(plan|prompt|mr)$", "", role).replace("-", "_")
    prompt = result["system_prompt"]
    result["system_prompt"] = _with_rules(prompt, active_dir, rules_key)
    result["model"] = config["models"].get(rules_key)
    return result
