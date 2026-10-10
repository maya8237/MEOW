"""
meow/native_prepare.py

Worktree/clean-tree bootstrapping and plan/review directory resolution for
native (in-Claude-Code-session) execution -- the `prepare`, `latest_plan`,
`latest_review`, and `verdict` helpers exposed by `native.native` for
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
from meow.project.config import config_root, load_config
from meow.project.config_models import LintCommand
from meow.project.onboarding import CONFIG_RELPATH, onboard_if_needed
from meow.project.plan_files import (
    _detect_review_flavor,
    _latest_plan_file,
    _latest_review_file,
    planned_plan_file,
    reject_report_name,
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
    fresh: bool = False


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
    plan_file = planned_plan_file(docs_dir, name)
    return plan_file, plan_file.with_name(plan_file.stem + "-review.md")


def _resolve_active_dir(
    working_dir: Path, options: PrepareOptions
) -> tuple[Path, bool, str | None]:
    """Pick the directory to work in: a branch worktree (issue flow), an
    existing-branch worktree (branch-review flow), a feature worktree
    (run/plan), or the project itself."""
    if options.branch:
        if not options.name:
            raise ValueError("--name is required together with --branch")
        return (
            _ensure_branch_worktree(working_dir, options.name, options.branch),
            True,
            options.name,
        )
    if options.existing_branch:
        if not options.use_worktree:
            _require_branch_checked_out(working_dir, options.existing_branch)
            return working_dir, False, options.name
        if not options.name:
            raise ValueError("--name is required together with --existing-branch")
        return (
            _ensure_existing_branch_worktree(
                working_dir, options.name, options.existing_branch
            ),
            True,
            options.name,
        )
    active_dir, name, is_worktree = _resolve_working_dir(
        working_dir,
        use_worktree=options.use_worktree,
        feature_name=options.name,
        source_branch=options.source_branch,
        fresh=options.fresh,
    )
    return active_dir, is_worktree, name


def _onboard(
    working_dir: Path, active_dir: Path, options: PrepareOptions, config: dict
) -> tuple[dict | None, dict]:
    """Onboard a never-onboarded project; return the report and fresh config.

    Read-only branch reviews onboard the project itself, never the reviewed
    branch's worktree.
    """
    onboard_dir = working_dir if options.existing_branch else active_dir
    report = onboard_if_needed(onboard_dir, working_dir)
    if report and CONFIG_RELPATH in report["files"]:
        config = load_config(onboard_dir)
    return report, config


def prepare(working_dir: Path, options: PrepareOptions) -> dict:
    """Run the CLI's startup guards and return everything a skill needs.

    Mirrors `cli_main` + `orchestrator._prepare_sprint`: the clean-tree
    check (skipped, like the CLI, when a worktree is built from an explicit
    source branch), `.gitignore` upkeep, then worktree resolution.
    """
    if not (options.branch or options.existing_branch):
        reject_report_name(options.name)
    config = load_config(working_dir)
    worktree_wanted = options.use_worktree or bool(options.branch)
    skip_clean = options.use_worktree and options.source_branch
    if options.require_clean and not skip_clean:
        _ensure_clean_tree(working_dir)
    _boot_repo(working_dir, include_gitignore=worktree_wanted)

    active_dir, is_worktree, name = _resolve_active_dir(working_dir, options)
    onboarding, config = _onboard(working_dir, active_dir, options, config)
    docs_dir = active_dir / config["docs_dir"]
    plan_file, review_file = _plan_paths(docs_dir, name)
    if options.fresh:
        # Same guard `run_sprint` applies before planning: don't hand a skill a
        # plan path that another active run still owns.
        from meow.execution.run_state import RunStore
        from meow.execution.sprint_runner import _owner_finished
        from meow.project.plan_state import PlanStore

        PlanStore(active_dir).assert_available(
            plan_file, lambda owner: _owner_finished(RunStore(working_dir), owner)
        )
    return {
        "name": name,
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
        "onboarding": onboarding,
    }


def latest_plan(working_dir: Path, active_dir: Path) -> dict:
    config = load_config(config_root(working_dir, active_dir))
    plan_file = _latest_plan_file(active_dir / config["docs_dir"])
    return {
        "plan_file": str(plan_file),
        "review_file": str(plan_file.with_name(plan_file.stem + "-review.md")),
    }


def latest_review(working_dir: Path, active_dir: Path) -> dict:
    config = load_config(config_root(working_dir, active_dir))
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
