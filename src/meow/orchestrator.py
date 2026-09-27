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

import shutil
import subprocess
from pathlib import Path

from meow.agents.explorer import make_explorer_agent
from meow.agents.generator import Generator
from meow.agents.planner import run_planner
from meow.agents.reviewer import run_prompt_reviewer, run_reviewer
from meow.config import (
    DEFAULT_CONFIG,
    LintCommand,
    _lint_entry,
    _normalize_lint_commands,
    load_config,
)
from meow.lint import make_lint_hook
from meow.sprint import Sprint

assert DEFAULT_CONFIG and _lint_entry and _normalize_lint_commands  # re-exported


def log_working_directory(working_dir: Path) -> None:
    """Print the active working directory once for each meow execution."""
    print(f"[meow] working directory: {Path(working_dir).resolve()}")


def _ensure_gitignore_entry(working_dir: Path, entry: str = ".worktrees/") -> None:
    """Ensure the repo's .gitignore includes the given ignored path."""
    gitignore = working_dir / ".gitignore"
    contents = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    lines = contents.splitlines()
    normalized = {line.strip() for line in lines}
    if entry in normalized or entry.rstrip("/") in normalized:
        return

    with gitignore.open("a", encoding="utf-8") as handle:
        if contents and not contents.endswith("\n"):
            handle.write("\n")
        handle.write(f"{entry}\n")


def _boot_repo(working_dir: Path, *, include_gitignore: bool = True) -> None:
    """Run working-directory boot checks every meow command needs."""
    if include_gitignore:
        _ensure_gitignore_entry(working_dir)


def _resolve_working_dir(
    working_dir: Path,
    *,
    use_worktree: bool,
    feature_name: str | None,
) -> tuple[Path, str | None, bool]:
    """Return the active directory and optional feature name."""
    if not use_worktree:
        return working_dir, feature_name, False
    if not feature_name:
        raise ValueError("feature_name is required when use_worktree=True")

    return _ensure_feature_worktree(working_dir, feature_name), feature_name, True


def _ensure_feature_worktree(working_dir: Path, feature_name: str) -> Path:
    """Create a per-feature worktree under the repo's .worktrees directory."""
    worktrees_dir = working_dir / ".worktrees"
    worktree_dir = worktrees_dir / feature_name

    if worktree_dir.exists():
        return worktree_dir

    worktrees_dir.mkdir(parents=True, exist_ok=True)

    git = shutil.which("git")
    if git:
        try:
            subprocess.run(
                [
                    git,
                    "-C",
                    str(working_dir),
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
    repo_dir: Path, config: dict, working_dir: Path | None = None
) -> Sprint:
    commands = config["lint"]
    active_dir = working_dir or repo_dir
    return Sprint(
        repo_dir=repo_dir,
        config=config,
        explorer=make_explorer_agent(config, active_dir),
        lint_hook=make_lint_hook(active_dir, commands),
        working_dir=active_dir,
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _describe_lint_plan(commands: list[LintCommand]) -> None:
    """Report the configured lint commands before a sprint spends anything."""
    for entry in commands:
        roles = ["per-file" if entry.per_file else "project-only"]
        roles.append("gate" if entry.gate else "non-blocking")
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
            summary = "\n".join(
                line.strip()
                for line in verdict.splitlines()
                if line.strip() and not line.startswith("STATUS:")
            )[:400]
            print(f"[reviewer] round {round_num}: {status}")
            if summary:
                print(f"[reviewer] round {round_num} summary: {summary}")

            if status == "PASS":
                return True

            instruction = (
                "The reviewer found issues. Fix them, then stop. "
                f"Reviewer feedback:\n{verdict}"
            )

    return False


def _review_summary(verdict: str) -> str | None:
    return next(
        (
            line.strip()
            for line in verdict.splitlines()
            if line.strip().startswith("SUMMARY:")
        ),
        None,
    )


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
    if summary_line := _review_summary(verdict):
        print(f"[reviewer] round 1 summary: {summary_line}")
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
            summary = "\n".join(
                line.strip()
                for line in verdict.splitlines()
                if line.strip() and not line.startswith("STATUS:")
            )[:400]
            print(f"[reviewer] round {round_num}: {status}")
            if summary:
                print(f"[reviewer] round {round_num} summary: {summary}")

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
    working_dir: Path,
    feature_name: str | None,
    request: str,
    *,
    use_worktree: bool = True,
    plan_file: Path | None = None,
):
    config = load_config(working_dir)
    _describe_lint_plan(config["lint"])
    active_dir, effective_name, is_worktree = _resolve_working_dir(
        working_dir,
        use_worktree=use_worktree,
        feature_name=feature_name,
    )
    sprint = _build_sprint(working_dir, config, active_dir if is_worktree else None)

    if plan_file is None:
        label = f" '{effective_name}'" if effective_name else ""
        print(f"[planner] planning{label} in {active_dir}...")
        plan_file = await run_planner(sprint, effective_name, request)
        print(f"[planner] wrote {plan_file}")

    if await _run_rounds(sprint, plan_file):
        label = f" '{feature_name}'" if feature_name else ""
        print(f"[orchestrator] sprint{label} complete.")
        return

    raise RuntimeError(
        f"Sprint{f' {feature_name!r}' if feature_name else ''} did not pass "
        f"after {config['max_rounds']} "
        "rounds -- stopping instead of looping forever. Inspect the review "
        "file."
    )


async def run_plan(  # ruff: ignore[too-many-arguments]
    working_dir: Path,
    feature_name: str | None,
    request: str,
    *,
    use_worktree: bool = True,
) -> Path:
    config = load_config(working_dir)
    active_dir, effective_name, is_worktree = _resolve_working_dir(
        working_dir,
        use_worktree=use_worktree,
        feature_name=feature_name,
    )
    sprint = _build_sprint(working_dir, config, active_dir if is_worktree else None)

    label = f" '{effective_name}'" if effective_name else ""
    print(f"[planner] planning{label} in {active_dir}...")
    plan_file = await run_planner(sprint, effective_name, request)
    print(f"[planner] wrote {plan_file}")
    return plan_file


async def run_review(
    working_dir: Path,
    plan_file: Path | None,
):
    config = load_config(working_dir)
    _describe_lint_plan(config["lint"])

    active_dir = working_dir
    if plan_file is not None:
        resolved_plan_file = plan_file
    else:
        resolved_plan_file = _latest_plan_file(active_dir / config["docs_dir"])
    sprint = _build_sprint(working_dir, config)

    print(f"[orchestrator] reviewing {resolved_plan_file} in {active_dir}...")
    if await _run_review_rounds(sprint, resolved_plan_file):
        print(f"[orchestrator] review of {resolved_plan_file} complete.")
        return

    raise RuntimeError(
        f"Review of {resolved_plan_file} did not pass after "
        f"{config['max_rounds']} rounds -- stopping instead of looping "
        "forever. Inspect the review file."
    )


async def run_prompt_review(  # ruff: ignore[too-many-arguments]
    working_dir: Path,
    prompt: str | None,
):
    """Review the current implementation against a free-text prompt.

    Unlike `run_review`, there is no Sprint Contract task list to loop a
    generator against, so this reports PASS/FAIL rather than gating on it.
    """
    config = load_config(working_dir)
    _describe_lint_plan(config["lint"])
    active_dir = working_dir
    sprint = _build_sprint(working_dir, config)

    print(f"[orchestrator] reviewing prompt in {active_dir}...")
    status, _ = await run_prompt_reviewer(sprint, prompt)
    review_file = (
        sprint.active_working_dir()
        / config["docs_dir"]
        / "review.md"
    )
    print(f"[orchestrator] review {status}: {review_file}")


def cli_main():
    """Keep the module entry point aligned with the installed CLI."""
    from meow.cli import cli_main as run_cli

    run_cli()


if __name__ == "__main__":
    cli_main()
