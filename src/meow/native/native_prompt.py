"""
meow/native_prompt.py

Prompt/agent-wiring construction for native (in-Claude-Code-session)
execution -- the `role_prompt` helper exposed by `native.native` for
`native_cli.py` to wire to `meow native prompt`. Split out on its own
because building the exact system prompt (and task message, where there is
one) a Task subagent needs to behave like an SDK role is a distinct concern
from directory bootstrapping (`native_prepare.py`), lint execution
(`native_lint.py`), or round-counter persistence (`native_state.py`).
"""

from pathlib import Path

from meow.agents.base import ProjectContext
from meow.agents.reviewer import (
    REMOTE_REVIEW_SPECS,
    _branch_diff,
    _git_review_context,
    new_review_filename,
)
from meow.project.config import load_config
from meow.project.prompts import (
    branch_review_prompt,
    explorer_prompt,
    generator_prompt,
    lint_fixer_prompt,
    plan_review_prompt,
    planner_prompt,
    prompt_review_prompt,
    remote_review_prompt,
    review_fixer_prompt,
)
from meow.project.shaping import ShapeContext, load_shape_artifact


def _load_shape_context(path: Path | None) -> ShapeContext | None:
    if path is None or not path.is_file():
        return None
    artifact = load_shape_artifact(path)
    if not hasattr(artifact, "chosen_approach"):
        return None
    return ShapeContext(str(path), artifact.chosen_approach, artifact.assumptions)


PROMPT_ROLES = (
    "planner",
    "generator",
    "explorer",
    "reviewer-plan",
    "reviewer-prompt",
    "reviewer-mr",
    "reviewer-branch",
    "review-fixer",
    "lint-fixer",
)


def _plan_review(
    context: ProjectContext,
    plan_file: Path,
    focus: str | None,
    shape_context: ShapeContext | None = None,
) -> dict:
    review_file = plan_file.with_name(plan_file.stem + "-review.md")
    text = plan_review_prompt(
        plan_file,
        review_file,
        context.lint_commands(),
        focus=focus,
        check_worktree_hygiene=context.use_worktree,
        shape_context=shape_context,
    )
    return {
        "system_prompt": text,
        "query": f"Review {plan_file}",
        "review_file": str(review_file),
    }


def _prompt_review(context: ProjectContext, basis: str | None) -> dict:
    docs_dir = context.config["docs_dir"]
    review_dir = context.active_working_dir() / docs_dir
    review_file = review_dir / new_review_filename("prompt")
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


def _mr_review(context: ProjectContext, provider: str) -> dict:
    try:
        flavor, request_label = REMOTE_REVIEW_SPECS[provider]
    except KeyError as exc:
        raise ValueError(f"unsupported remote review provider: {provider}") from exc
    review_dir = context.active_working_dir() / context.config["docs_dir"]
    review_file = review_dir / new_review_filename(flavor)
    text = remote_review_prompt(
        review_file, request_label, check_worktree_hygiene=context.use_worktree
    )
    return {"system_prompt": text, "query": None, "review_file": str(review_file)}


def _branch_review(context: ProjectContext, target: str, branch: str) -> dict:
    docs_dir = context.config["docs_dir"]
    review_dir = context.active_working_dir() / docs_dir
    review_file = review_dir / new_review_filename("branch")
    diff_text = _branch_diff(context.active_working_dir(), target, branch)
    text = branch_review_prompt(
        target,
        branch,
        review_file,
        context.lint_commands(),
        check_worktree_hygiene=context.use_worktree,
    )
    query = (
        f"Diff of branch {branch!r} against target {target!r} (git diff "
        f"{target}...{branch}, including any uncommitted changes):\n\n"
        + (diff_text or "(no diff -- branch matches target)")
    )
    return {"system_prompt": text, "query": query, "review_file": str(review_file)}


def _simple_prompt(
    role: str,
    context: ProjectContext,
    plan_file: Path | None,
    shape_context: ShapeContext | None = None,
) -> dict:
    active_dir = context.active_working_dir()
    if role in {"planner", "generator"} and plan_file is None:
        raise ValueError(f"the {role} prompt needs --plan")
    builders = {
        "planner": lambda: planner_prompt(plan_file, shape_context),
        "generator": lambda: generator_prompt(plan_file),
        "explorer": lambda: explorer_prompt(active_dir),
        "review-fixer": review_fixer_prompt,
        "lint-fixer": lint_fixer_prompt,
    }
    return {"system_prompt": builders[role](), "query": None}


def _reviewer_prompt(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] -- mirrors the `meow native prompt` flags one to one
    role: str,
    context: ProjectContext,
    plan_file: Path | None,
    focus: str | None,
    target: str | None,
    branch: str | None,
    remote_provider: str,
    shape_context: ShapeContext | None = None,
) -> dict:
    if role == "reviewer-plan":
        if plan_file is None:
            raise ValueError("the reviewer-plan prompt needs --plan")
        return _plan_review(context, plan_file, focus, shape_context)
    if role == "reviewer-prompt":
        return _prompt_review(context, focus)
    if role == "reviewer-mr":
        return _mr_review(context, remote_provider)
    if not target or not branch:
        raise ValueError("the reviewer-branch prompt needs --target and --branch")
    return _branch_review(context, target, branch)


def role_prompt(  # ruff: ignore[too-many-arguments] -- mirrors the `meow native prompt` flags one to one
    working_dir: Path,
    active_dir: Path,
    role: str,
    *,
    plan_file: Path | None = None,
    focus: str | None = None,
    use_worktree: bool = False,
    target: str | None = None,
    branch: str | None = None,
    remote_provider: str = "gitlab",
    shape_path: Path | None = None,
) -> dict:
    """The exact system prompt (and task message, where there is one) the SDK
    agent for `role` would use, so a Task subagent launched with it behaves
    like the SDK role."""
    if role not in PROMPT_ROLES:
        raise ValueError(f"role must be one of {PROMPT_ROLES}, got {role!r}")
    config = load_config(working_dir)
    context = ProjectContext(active_dir, config, use_worktree=use_worktree)
    shape_context = _load_shape_context(shape_path)
    if role.startswith("reviewer-"):
        result = _reviewer_prompt(
            role,
            context,
            plan_file,
            focus,
            target,
            branch,
            remote_provider,
            shape_context,
        )
    else:
        result = _simple_prompt(role, context, plan_file, shape_context)
    model_role = role.split("-", 1)[0] if role.startswith("reviewer-") else role
    rules_key = model_role.replace("-", "_")
    result["model"] = config["models"].get(rules_key)
    return result
