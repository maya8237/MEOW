"""`meow native prompt`: the exact system prompt, task message and model an
SDK role would use, for a Task subagent to run with."""

import asyncio
from collections.abc import Sequence
from pathlib import Path

from meow.agents.base import Agent, ProjectContext
from meow.agents.reviewer import (
    REMOTE_REVIEW_SPECS,
    _branch_diff,
    _git_review_context,
    _prepare_review_file,
    branch_review_query,
    plan_review_query,
    prompt_review_query,
    remote_review_query,
)
from meow.infrastructure.lint import check_lint_evidence
from meow.native.native_models import resolve_native_model
from meow.native.native_review import bind_review_checks
from meow.project.config import config_root, load_config
from meow.project.plan_files import new_review_filename, plan_review_file
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
from meow.project.review_scope import prepare_scope, scope_instructions
from meow.project.shaping import ShapeContext, load_shape_artifact


def _load_shape_context(path: Path | None) -> ShapeContext | None:
    if path is None or not path.is_file():
        return None
    artifact = load_shape_artifact(path)
    if not hasattr(artifact, "chosen_approach"):
        return None
    return ShapeContext(str(path), artifact.chosen_approach, artifact.assumptions)


def _lint_report(context: ProjectContext) -> str:
    """The same harness lint evidence the SDK reviewer receives."""
    evidence = asyncio.run(
        check_lint_evidence(
            context.active_working_dir(),
            context.lint_commands(),
            context.config["lint_timeout"],
        )
    )
    return evidence.report()


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
    review_file = plan_review_file(plan_file)
    text = plan_review_prompt(
        plan_file,
        review_file,
        context.lint_commands(),
        focus=focus,
        check_worktree_hygiene=context.use_worktree,
        shape_context=shape_context,
        agent=Agent(context).definition("reviewer"),
    )
    return {
        "system_prompt": text,
        "query": plan_review_query(plan_file, _lint_report(context)),
        "review_file": str(review_file),
    }


def _prompt_review(context: ProjectContext, basis: str | None) -> dict:
    docs_dir = context.config["docs_dir"]
    review_dir = context.active_working_dir() / docs_dir
    review_file = review_dir / new_review_filename("prompt")
    git_context, has_diff = _git_review_context(context)
    scope = prepare_scope(
        review_file,
        context.active_working_dir(),
        docs_dir,
        basis,
        has_diff,
        context.config.get("_review_scope", "auto"),
    )
    contract = scope_instructions(scope)
    text = prompt_review_prompt(
        (basis or "").strip(),
        review_file,
        context.lint_commands(),
        has_diff=has_diff,
        docs_dir=docs_dir,
        check_worktree_hygiene=context.use_worktree,
        agent=Agent(context).definition("reviewer"),
    )
    query = prompt_review_query(
        (basis or "").strip(),
        git_context,
        has_diff=has_diff,
        docs_dir=docs_dir,
        lint_report=_lint_report(context),
    )
    return {
        "system_prompt": text + contract,
        "query": query + contract,
        "review_file": str(review_file),
        "scope": scope["mode"],
    }


def _mr_review(context: ProjectContext, provider: str) -> dict:
    try:
        flavor, request_label = REMOTE_REVIEW_SPECS[provider]
    except KeyError as exc:
        raise ValueError(f"unsupported remote review provider: {provider}") from exc
    review_dir = context.active_working_dir() / context.config["docs_dir"]
    review_file = review_dir / new_review_filename(flavor)
    text = remote_review_prompt(
        review_file,
        request_label,
        check_worktree_hygiene=context.use_worktree,
        agent=Agent(context).definition("reviewer"),
    )
    # The skill fetches the request itself, so it gets the exact SDK task
    # message with placeholders to fill in.
    template = remote_review_query(request_label, "<title>", "<description>", "<diff>")
    return {
        "system_prompt": text,
        "query": None,
        "query_template": template,
        "review_file": str(review_file),
    }


def _branch_review(
    context: ProjectContext, target: str, branch: str, objective: str | None = None
) -> dict:
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
        objective=objective,
        agent=Agent(context).definition("reviewer"),
    )
    query = branch_review_query(target, branch, diff_text, _lint_report(context))
    return {"system_prompt": text, "query": query, "review_file": str(review_file)}


def _simple_prompt(
    role: str,
    context: ProjectContext,
    plan_file: Path | None,
    shape_context: ShapeContext | None = None,
) -> dict:
    active_dir = context.active_working_dir()
    agent = Agent(context).definition(role)
    if role in {"planner", "generator"} and plan_file is None:
        raise ValueError(f"the {role} prompt needs --plan")
    builders = {
        "planner": lambda: planner_prompt(plan_file, shape_context, agent=agent),
        "generator": lambda: generator_prompt(plan_file, agent=agent),
        "explorer": lambda: explorer_prompt(active_dir, agent=agent),
        "review-fixer": lambda: review_fixer_prompt(agent=agent),
        "lint-fixer": lambda: lint_fixer_prompt(agent=agent),
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


def role_prompt(  # ruff: ignore[too-many-arguments, too-many-statements] -- mirrors native flags and role metadata
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
    host: str | None = None,
    available_models: Sequence[str] | None = None,
    scope: str = "auto",
) -> dict:
    """The exact system prompt (and task message, where there is one) the SDK
    agent for `role` would use, so a Task subagent launched with it behaves
    like the SDK role."""
    if role not in PROMPT_ROLES:
        raise ValueError(f"role must be one of {PROMPT_ROLES}, got {role!r}")
    if scope not in {"auto", "project", "diff"} or (
        scope != "auto" and role != "reviewer-prompt"
    ):
        raise ValueError(
            "--scope requires reviewer-prompt and must be auto, project, or diff"
        )
    config = load_config(config_root(working_dir, active_dir))
    config["_review_scope"] = scope
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
    if role.startswith("reviewer-") and role != "reviewer-mr":
        _prepare_review_file(Path(result["review_file"]))
        result["verification"] = bind_review_checks(
            Path(result["review_file"]),
            working_dir,
            active_dir,
            project_scope=result.get("scope") == "project",
            plan_file=plan_file if role == "reviewer-plan" else None,
        )
        result["query"] += (
            "\n\nCompletion requires current configured lint, test, and build checks. "
            "Run `meow native review-checks --background` for this active directory, "
            "inspect `review-checks --status`, then use `meow native verdict` "
            "to validate this report. Missing, running, failed, interrupted, "
            "or stale checks block PASS."
        )
    model_role = role.split("-", 1)[0] if role.startswith("reviewer-") else role
    # Same lookup (and reviewer fallback) as the SDK role.
    model_role = model_role.replace("-", "_")
    base = Agent(context)
    agent = base.definition(model_role.replace("_", "-"))
    result.update(
        resolve_native_model(
            context.model(model_role, agent),
            host=host,
            available_models=available_models,
        )
    )
    tools = list(agent.tools or ())
    if role == "reviewer-mr":
        tools = [tool for tool in tools if tool in {"Read", "Grep", "Glob", "Write"}]
    result.update(
        tools=tools,
        skills=base.skills(agent.role, list(agent.skills)),
        agents=list(base.delegates(agent, tools)),
        session=agent.session,
    )
    return result
