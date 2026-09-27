"""
meow/orchestrator.py

The generic harness engine: explorer, planner, generator, and reviewer as
real peer agents, coordinated by plain Python control flow. Unlike the
single-project version, all project-specific values (lint commands, models,
round cap) are read from a `.harness.toml` file in the target project's
root, not hardcoded here -- this file is meant to be installed once and
reused across projects.

A project may configure any number of lint commands. Each one declares
whether it runs per edited file, whether it can auto-fix, and whether its
failure is allowed to fail a sprint -- see `_normalize_lint_commands`.

Install (from the meow repo root):    pip install -e .
Run (from inside a project repo):        meow run "Add CSV export"
"""

import argparse
import asyncio
import re
import shutil
import subprocess
from pathlib import Path

from meow.config import (
    DEFAULT_CONFIG,
    LintCommand,
    _lint_entry,
    _normalize_lint_commands,
    load_config,
)
from meow.lint import make_lint_hook
from meow.roles import (
    Generator,
    make_explorer_agent,
    run_planner,
    run_prompt_reviewer,
    run_reviewer,
)
from meow.sprint import Sprint

assert DEFAULT_CONFIG and _lint_entry and _normalize_lint_commands  # re-exported


def _ensure_gitignore_entry(project_root: Path, entry: str = ".worktrees/") -> None:
    """Ensure the repo's .gitignore includes the given ignored path."""
    gitignore = project_root / ".gitignore"
    contents = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    lines = contents.splitlines()
    normalized = {line.strip() for line in lines}
    if entry in normalized or entry.rstrip("/") in normalized:
        return

    with gitignore.open("a", encoding="utf-8") as handle:
        if contents and not contents.endswith("\n"):
            handle.write("\n")
        handle.write(f"{entry}\n")


def _boot_repo(project_root: Path, *, include_gitignore: bool = True) -> None:
    """Run the repository-level boot checks every meow command needs."""
    if include_gitignore:
        _ensure_gitignore_entry(project_root)


def _resolve_worktree_root(
    project_root: Path,
    *,
    use_worktree: bool,
    worktree_name: str | None,
    default_name: str,
) -> tuple[Path, str, bool]:
    """Return the working directory and the effective worktree name."""
    if not use_worktree:
        return project_root, default_name, False

    resolved_name = worktree_name or default_name
    return _ensure_feature_worktree(project_root, resolved_name), resolved_name, True


def _ensure_feature_worktree(project_root: Path, feature_name: str) -> Path:
    """Create a per-feature worktree under the repo's .worktrees directory."""
    worktrees_root = project_root / ".worktrees"
    worktree_dir = worktrees_root / feature_name

    if worktree_dir.exists():
        return worktree_dir

    worktrees_root.mkdir(parents=True, exist_ok=True)

    git = shutil.which("git")
    if git:
        try:
            subprocess.run(
                [
                    git,
                    "-C",
                    str(project_root),
                    "worktree",
                    "add",
                    "--detach",
                    str(worktree_dir),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            return worktree_dir
        except subprocess.CalledProcessError:
            pass

    worktree_dir.mkdir(parents=True, exist_ok=True)
    return worktree_dir


def _build_sprint(
    project_root: Path, config: dict, worktree_root: Path | None = None
) -> Sprint:
    commands = config["lint"]
    return Sprint(
        project_root=project_root,
        config=config,
        explorer=make_explorer_agent(config),
        lint_hook=make_lint_hook(project_root, commands),
        working_directory=worktree_root,
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _describe_lint_plan(commands: list[LintCommand]) -> None:
    """Report the configured lint commands before a sprint spends anything."""
    for entry in commands:
        roles = ["per-file" if entry.per_file else "project-only"]
        roles.append("gate" if entry.gate else "advisory")
        if entry.fix_flag:
            roles.append(f"fix {entry.fix_flag}")
        print(f"[lint] {entry.command}  ({', '.join(roles)})")


async def _run_rounds(sprint: Sprint, plan_file: Path) -> bool:
    """Loop generator -> reviewer. True if the sprint passed."""
    max_rounds = sprint.config["max_rounds"]

    async with Generator(sprint, plan_file) as generator:
        instruction = f"Implement the tasks in {plan_file}."
        for round_num in range(1, max_rounds + 1):
            print(f"[generator] round {round_num}: implementing...")
            await generator.implement(instruction)

            print(f"[reviewer] round {round_num}: reviewing...")
            status, verdict = await run_reviewer(sprint, plan_file)
            print(f"[reviewer] round {round_num}: {status}")

            if status == "PASS":
                return True

            instruction = (
                "The reviewer found issues. Fix them, then stop. "
                f"Reviewer feedback:\n{verdict}"
            )

    return False


async def _run_review_rounds(sprint: Sprint, plan_file: Path) -> bool:
    """Loop reviewer -> generator, reviewing the existing code first.

    Unlike `_run_rounds`, this doesn't assume the plan is unimplemented --
    it only spins up a generator session if the first review actually finds
    something to fix. True if the plan ends up passing.
    """
    max_rounds = sprint.config["max_rounds"]

    print("[reviewer] round 1: reviewing...")
    status, verdict = await run_reviewer(sprint, plan_file)
    print(f"[reviewer] round 1: {status}")
    if status == "PASS":
        return True

    async with Generator(sprint, plan_file) as generator:
        instruction = (
            "The reviewer found issues. Fix them, then stop. "
            f"Reviewer feedback:\n{verdict}"
        )
        for round_num in range(2, max_rounds + 1):
            print(f"[generator] round {round_num}: implementing...")
            await generator.implement(instruction)

            print(f"[reviewer] round {round_num}: reviewing...")
            status, verdict = await run_reviewer(sprint, plan_file)
            print(f"[reviewer] round {round_num}: {status}")

            if status == "PASS":
                return True

            instruction = (
                "The reviewer found issues. Fix them, then stop. "
                f"Reviewer feedback:\n{verdict}"
            )

    return False


def _latest_plan_file(docs_dir: Path) -> Path:
    """The most recently modified sprint plan in docs_dir, excluding reviews."""
    candidates = [
        path for path in docs_dir.glob("*.md")
        if not path.name.endswith("-review.md")
    ]
    if not candidates:
        raise FileNotFoundError(
            f"No plan file found in {docs_dir}. Run `meow plan "
            '"<feature>"` first, or pass --plan-file explicitly.'
        )
    return max(candidates, key=lambda path: path.stat().st_mtime)


async def run_sprint(  # ruff: ignore[too-many-arguments]
    project_root: Path,
    feature_name: str,
    request: str,
    *,
    use_worktree: bool = True,
    worktree_name: str | None = None,
):
    config = load_config(project_root)
    _describe_lint_plan(config["lint"])
    worktree_root, effective_name, is_worktree = _resolve_worktree_root(
        project_root,
        use_worktree=use_worktree,
        worktree_name=worktree_name,
        default_name=feature_name,
    )
    sprint = _build_sprint(project_root, config, worktree_root if is_worktree else None)

    print(f"[planner] planning '{effective_name}' in {worktree_root}...")
    plan_file = await run_planner(sprint, effective_name, request)
    print(f"[planner] wrote {plan_file}")

    if await _run_rounds(sprint, plan_file):
        print(f"[orchestrator] sprint '{feature_name}' complete.")
        return

    raise RuntimeError(
        f"Sprint '{feature_name}' did not pass after {config['max_rounds']} "
        "rounds -- stopping instead of looping forever. Inspect the review "
        "file."
    )


async def run_plan(  # ruff: ignore[too-many-arguments]
    project_root: Path,
    feature_name: str,
    request: str,
    *,
    use_worktree: bool = True,
    worktree_name: str | None = None,
) -> Path:
    config = load_config(project_root)
    worktree_root, effective_name, is_worktree = _resolve_worktree_root(
        project_root,
        use_worktree=use_worktree,
        worktree_name=worktree_name,
        default_name=feature_name,
    )
    sprint = _build_sprint(project_root, config, worktree_root if is_worktree else None)

    print(f"[planner] planning '{effective_name}' in {worktree_root}...")
    plan_file = await run_planner(sprint, effective_name, request)
    print(f"[planner] wrote {plan_file}")
    return plan_file


async def run_review(
    project_root: Path,
    plan_file: Path | None,
    *,
    use_worktree: bool = True,
    worktree_name: str | None = None,
):
    config = load_config(project_root)
    _describe_lint_plan(config["lint"])

    resolved_plan_file = plan_file or _latest_plan_file(
        project_root / config["docs_dir"]
    )
    feature_name = worktree_name or resolved_plan_file.stem
    worktree_root, _, is_worktree = _resolve_worktree_root(
        project_root,
        use_worktree=use_worktree,
        worktree_name=worktree_name,
        default_name=feature_name,
    )
    sprint = _build_sprint(project_root, config, worktree_root if is_worktree else None)

    print(f"[orchestrator] reviewing {resolved_plan_file} in {worktree_root}...")
    if await _run_review_rounds(sprint, resolved_plan_file):
        print(f"[orchestrator] review of {resolved_plan_file} complete.")
        return

    raise RuntimeError(
        f"Review of {resolved_plan_file} did not pass after "
        f"{config['max_rounds']} rounds -- stopping instead of looping "
        "forever. Inspect the review file."
    )


async def run_prompt_review(  # ruff: ignore[too-many-arguments]
    project_root: Path,
    feature_name: str,
    prompt: str,
    *,
    use_worktree: bool = True,
    worktree_name: str | None = None,
):
    """Review the current implementation against a free-text prompt.

    Unlike `run_review`, there is no Sprint Contract task list to loop a
    generator against, so this reports PASS/FAIL rather than gating on it.
    """
    config = load_config(project_root)
    _describe_lint_plan(config["lint"])
    worktree_root, effective_name, is_worktree = _resolve_worktree_root(
        project_root,
        use_worktree=use_worktree,
        worktree_name=worktree_name,
        default_name=feature_name,
    )
    sprint = _build_sprint(project_root, config, worktree_root if is_worktree else None)

    print(f"[orchestrator] reviewing prompt in {worktree_root}...")
    status, _ = await run_prompt_reviewer(sprint, effective_name, prompt)
    review_file = project_root / config["docs_dir"] / f"{effective_name}-review.md"
    print(f"[orchestrator] review {status}: {review_file}")


# ---------------------------------------------------------------------------
# CLI entry point -- registered as the `meow` console script
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50]


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="meow")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run", help="Plan, implement, and review a feature request end to end."
    )
    run_parser.add_argument("request", help="Feature request text.")
    run_parser.add_argument(
        "--project-root", default=".",
        help="Path to the project repo (default: current directory).",
    )

    plan_parser = subparsers.add_parser(
        "plan",
        help="Write a sprint plan for a feature request, without implementing it.",
    )
    plan_parser.add_argument("request", help="Feature request text.")
    plan_parser.add_argument(
        "--project-root", default=".",
        help="Path to the project repo (default: current directory).",
    )

    review_parser = subparsers.add_parser(
        "review",
        help="Review an existing plan's implementation and fix any issues found.",
    )
    review_parser.add_argument(
        "--plan-file", default=None,
        help="Plan file to review (default: latest plan in docs_dir).",
    )
    review_parser.add_argument(
        "--project-root", default=".",
        help="Path to the project repo (default: current directory).",
    )

    return parser


def cli_main():
    args = _build_arg_parser().parse_args()
    project_root = Path(args.project_root).resolve()

    if args.command == "run":
        asyncio.run(run_sprint(project_root, _slugify(args.request), args.request))
    elif args.command == "plan":
        asyncio.run(run_plan(project_root, _slugify(args.request), args.request))
    elif args.command == "review":
        plan_file = Path(args.plan_file).resolve() if args.plan_file else None
        asyncio.run(run_review(project_root, plan_file))


if __name__ == "__main__":
    cli_main()
