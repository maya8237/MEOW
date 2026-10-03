"""
meow/native_prepare.py

Worktree/clean-tree bootstrapping and plan/review directory resolution for
native (in-Claude-Code-session) execution -- the `prepare`, `latest_plan`,
`latest_review`, and `verdict` helpers `native.py` re-exports for
`native_cli.py` to wire to `meow native prepare`/`latest-plan`/
`latest-review`/`verdict`. Split out on its own because "which directory a
skill run works in, and what plan/review files live there" is a distinct
concern from lint execution (`native_lint.py`), round-counter persistence
(`native_state.py`), or prompt construction (`native_prompt.py`).
"""

from dataclasses import dataclass
from pathlib import Path

from meow.agents.reviewer import _verdict_status
from meow.infrastructure.worktree import (
    _boot_repo,
    _ensure_branch_worktree,
    _ensure_clean_tree,
    _ensure_existing_branch_worktree,
    _require_branch_checked_out,
    _resolve_working_dir,
)
from meow.project.config import LintCommand, load_config
from meow.project.plan_files import (
    _detect_review_flavor,
    _latest_plan_file,
    _latest_review_file,
)


@dataclass(frozen=True)
class PrepareOptions:
    """What `prepare` needs beyond the project directory."""

    name: str | None = None
    use_worktree: bool = True
    source_branch: str | None = None
    branch: str | None = None
    existing_branch: str | None = None
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
    """Pick the directory to work in: a branch worktree (issue flow), an
    existing-branch worktree (branch-review flow), a feature worktree
    (run/plan), or the project itself."""
    if options.branch:
        if not options.name:
            raise ValueError("--name is required together with --branch")
        return _ensure_branch_worktree(working_dir, options.name, options.branch), True
    if options.existing_branch:
        if not options.use_worktree:
            _require_branch_checked_out(working_dir, options.existing_branch)
            return working_dir, False
        if not options.name:
            raise ValueError("--name is required together with --existing-branch")
        return (
            _ensure_existing_branch_worktree(
                working_dir, options.name, options.existing_branch
            ),
            True,
        )
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
