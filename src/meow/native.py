"""
meow/native.py

The deterministic half of native (in-Claude-Code-session) execution.

When a meow skill runs inside a Claude Code session, that session plays
planner/generator and dispatches reviewer/explorer subagents itself -- no
Agent SDK process is involved. What the session cannot do reliably from
prose alone are the mechanical facts the Python harness already owns:
parsed `.harness.toml`, worktree and branch resolution, plan/review
lookup, lint execution, verdict parsing, the round counter, and the role
prompts. This module exposes exactly those, reusing the same functions the
`meow run`/`review`/`cr` paths call, so both modes share one set of
conventions. Nothing here imports or starts the Agent SDK; `native_cli.py`
wires these functions to `meow native ...` and prints their results as JSON.
"""

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path

from meow.agents.base import ProjectContext
from meow.agents.reviewer import (
    MR_REVIEW_FILENAME,
    PROMPT_REVIEW_FILENAME,
    _git_review_context,
    _verdict_status,
)
from meow.config import LintCommand, load_config
from meow.lint import _run_lint_on_file, apply_lint_fixes, check_lint_commands
from meow.orchestrator import (
    _detect_review_flavor,
    _latest_plan_file,
    _latest_review_file,
)
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
from meow.worktree import (
    _boot_repo,
    _ensure_branch_worktree,
    _ensure_clean_tree,
    _push_branch,
    _resolve_working_dir,
)

STATE_SUFFIX = ".native-state.json"
RULE_ROLES = ("explorer", "planner", "generator", "reviewer")
ROUND_MODES = ("next", "reset", "show")
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


@dataclass(frozen=True)
class LintOptions:
    """What `lint` needs beyond the two directories."""

    file_path: str | None = None
    fix: bool = False
    all_blocking: bool = False


@dataclass(frozen=True)
class PrepareOptions:
    """What `prepare` needs beyond the project directory."""

    name: str | None = None
    use_worktree: bool = True
    source_branch: str | None = None
    branch: str | None = None
    require_clean: bool = True


def _lint_plan(commands: list[LintCommand]) -> list[dict]:
    return [
        {
            "command": entry.command,
            "fix_flag": entry.fix_flag,
            "per_file": entry.per_file,
            "gate": entry.gate,
        }
        for entry in commands
    ]


def _plan_paths(docs_dir: Path, name: str | None) -> tuple[Path, Path]:
    plan_file = docs_dir / (f"{name}.md" if name else "plan.md")
    return plan_file, plan_file.with_name(plan_file.stem + "-review.md")


def _resolve_active_dir(
    working_dir: Path, options: PrepareOptions
) -> tuple[Path, bool]:
    """Pick the directory to work in: a branch worktree (issue flow), a
    feature worktree (run/plan), or the project itself."""
    if options.branch:
        if not options.name:
            raise ValueError("--name is required together with --branch")
        return _ensure_branch_worktree(working_dir, options.name, options.branch), True
    active_dir, _, is_worktree = _resolve_working_dir(
        working_dir,
        use_worktree=options.use_worktree,
        feature_name=options.name,
        source_branch=options.source_branch,
    )
    return active_dir, is_worktree


def prepare(working_dir: Path, options: PrepareOptions) -> dict:
    """Run the CLI's startup guards and return everything a skill needs.

    Mirrors `cli_main` + `orchestrator._prepare_sprint`: the clean-tree
    check (skipped, like the CLI, when a worktree is built from an explicit
    source branch), `.gitignore` upkeep, then worktree resolution.
    """
    config = load_config(working_dir)
    worktree_wanted = options.use_worktree or bool(options.branch)
    skip_clean = options.use_worktree and options.source_branch
    if options.require_clean and not skip_clean:
        _ensure_clean_tree(working_dir)
    _boot_repo(working_dir, include_gitignore=worktree_wanted)

    active_dir, is_worktree = _resolve_active_dir(working_dir, options)
    docs_dir = active_dir / config["docs_dir"]
    plan_file, review_file = _plan_paths(docs_dir, options.name)
    return {
        "project_dir": str(working_dir),
        "active_dir": str(active_dir),
        "use_worktree": is_worktree,
        "docs_dir": str(docs_dir),
        "plan_file": str(plan_file),
        "review_file": str(review_file),
        "max_rounds": config["max_rounds"],
        "lint_timeout": config["lint_timeout"],
        "models": config["models"],
        "lint": _lint_plan(config["lint"]),
        "rules": {
            role: load_rules(active_dir, role) for role in RULE_ROLES
        },
    }


def latest_plan(working_dir: Path, active_dir: Path) -> dict:
    config = load_config(working_dir)
    plan_file = _latest_plan_file(active_dir / config["docs_dir"])
    return {
        "plan_file": str(plan_file),
        "review_file": str(plan_file.with_name(plan_file.stem + "-review.md")),
    }


def latest_review(working_dir: Path, active_dir: Path) -> dict:
    config = load_config(working_dir)
    review_file = _latest_review_file(active_dir / config["docs_dir"])
    return {
        "review_file": str(review_file),
        "flavor": _detect_review_flavor(review_file),
    }


def verdict(review_file: Path) -> dict:
    """PASS/FAIL and summary of a review file, the way the CLI reads one."""
    text = review_file.read_text(encoding="utf-8")
    summary = next(
        (
            line.strip().removeprefix("SUMMARY:").strip()
            for line in text.splitlines()
            if line.strip().startswith("SUMMARY:")
        ),
        None,
    )
    return {"status": _verdict_status(text), "summary": summary}


async def _lint_one_file(
    active_dir: Path, commands: list[LintCommand], file_path: str, timeout: float
) -> list[str]:
    per_file = [entry for entry in commands if entry.per_file]
    try:
        return await _run_lint_on_file(active_dir, per_file, file_path, timeout)
    except OSError as exc:
        return [f"Could not run lint on {file_path}: {exc}"]


async def _lint_project(
    active_dir: Path,
    commands: list[LintCommand],
    timeout: float,
    *,
    all_blocking: bool,
) -> dict:
    blocking: list[str] = []
    informational: list[str] = []
    for entry in commands:
        try:
            problems = await check_lint_commands(active_dir, [entry], timeout)
        except OSError as exc:
            problems = [f"$ {entry.command}\nCould not run: {exc}"]
        (blocking if all_blocking or entry.gate else informational).extend(problems)
    return {"clean": not blocking, "blocking": blocking, "informational": informational}


def lint(working_dir: Path, active_dir: Path, options: LintOptions) -> dict:
    """Run the configured lint plan in `active_dir`.

    With `options.file_path`, behave like the SDK generator's post-edit hook:
    run each per-file command (with its fix flag) on that file and report
    what could not be auto-fixed. Otherwise run every command project-wide in
    check-only mode, split into blocking (`gate`) and informational
    findings; `options.fix` first applies each command's own fix flag.

    `options.all_blocking` treats every command as blocking regardless of
    `gate`, ignoring the review-only gate/informational split: CLI mode's
    `meow lint-fix` fixes/reports every configured command unconditionally
    (`gate` only means "this command's failure fails a sprint review"), so
    the `lint-fix` skill's native mode passes `all_blocking=True` to match
    that CLI behavior instead of silently skipping non-gate commands.
    """
    config = load_config(working_dir)
    commands, timeout = config["lint"], config["lint_timeout"]
    if options.file_path:
        problems = asyncio.run(
            _lint_one_file(active_dir, commands, options.file_path, timeout)
        )
        return {"clean": not problems, "problems": problems}
    if options.fix:
        asyncio.run(apply_lint_fixes(active_dir, commands, timeout))
    return asyncio.run(
        _lint_project(active_dir, commands, timeout, all_blocking=options.all_blocking)
    )


def _state_file(plan_file: Path) -> Path:
    return plan_file.with_name(plan_file.stem + STATE_SUFFIX)


def _read_round(state_file: Path) -> int:
    """The stored round number; a missing or unreadable file means 0."""
    try:
        value = json.loads(state_file.read_text(encoding="utf-8"))["round"]
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    return value if isinstance(value, int) and value >= 0 else 0


def round_state(plan_file: Path, max_rounds: int, mode: str) -> dict:
    """Advance, reset, or read the on-disk round counter for a plan.

    `next` starts a new round unless `max_rounds` rounds already ran, in
    which case it leaves the counter alone and reports `exhausted` -- the
    skill must stop and report instead of looping. The counter lives beside
    the plan (not in the model's memory) so it survives context compaction.
    """
    if mode not in ROUND_MODES:
        raise ValueError(f"mode must be one of {ROUND_MODES}, got {mode!r}")
    state_file = _state_file(plan_file)
    current = _read_round(state_file)
    if mode == "reset":
        current = 0
    exhausted = mode == "next" and current >= max_rounds
    if mode == "next" and not exhausted:
        current += 1
    if mode != "show" and not exhausted:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"round": current}), encoding="utf-8")
    return {"round": current, "max_rounds": max_rounds, "exhausted": exhausted}


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


def push(active_dir: Path, branch: str) -> dict:
    _push_branch(active_dir, branch)
    return {"branch": branch, "pushed": True}
