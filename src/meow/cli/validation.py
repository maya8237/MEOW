"""Flag validation for `meow` subcommands: each check rejects an invalid
flag combination through the parser so the error reads as a usage error."""

import argparse
import re
from pathlib import Path

from meow.execution.run_policy import _MISUSE_CHECKS, _is_plain_build, _misused_flags
from meow.infrastructure.worktree import _is_linked_worktree


def _validate_feature_name_requirement(
    parser: argparse.ArgumentParser, args, working_dir: Path
) -> None:
    requires_name = (
        (args.command == "plan" or _is_plain_build(args))
        and not args.no_worktree
        # --work-dir already pointing at a linked worktree means "work
        # here", not "nest another worktree inside it" -- _resolve_working_dir
        # treats this exactly like --no-worktree, so the upfront requirement
        # must too, or a --name that will be silently ignored is demanded
        # for no reason.
        and not _is_linked_worktree(working_dir)
    )
    if requires_name and args.feature_name is None:
        parser.error("--name/-f is required unless --no-worktree/-n is supplied")


def _validate_lint_fix_flags(parser: argparse.ArgumentParser, args) -> None:
    if args.request or args.jira is not None:
        parser.error("--lint-fix takes no request text and no --jira")
    if getattr(args, "test", False):
        parser.error("--test cannot be combined with --lint-fix")
    checks = (
        *_MISUSE_CHECKS,
        ("-m/--manually-approve-plan", lambda a: a.manually_approve_plan),
    )
    misused = _misused_flags(args, checks)
    if misused:
        joined = ", ".join(misused)
        parser.error(f"--lint-fix doesn't use a worktree or a plan; drop {joined}")


def _validate_build_flags(parser: argparse.ArgumentParser, args) -> None:
    if args.request and args.jira is not None:
        parser.error("Give either a request or --jira, not both")
    if not args.request and args.jira is None:
        parser.error("request is required unless --jira is given")
    if args.jira is not None:
        misused = _misused_flags(args, _MISUSE_CHECKS)
        if misused:
            parser.error(
                "--jira uses its own pushable-branch worktree; drop "
                + ", ".join(misused)
            )


def _validate_workflow_flags(parser, args):
    try:
        parse_inputs(args.input)
    except ValueError as exc:
        parser.error(str(exc))
    if args.lint_fix and (args.workflow or args.input):
        parser.error("--workflow and --input require a feature run")


def _validate_run_flags(parser: argparse.ArgumentParser, args) -> None:
    if args.command != "run":
        return
    _validate_workflow_flags(parser, args)
    if args.background and not args.auto:
        parser.error("--background requires --auto")
    if args.auto and args.manually_approve_plan:
        parser.error("--auto cannot wait for manual plan approval")
    if args.auto and args.lint_fix:
        parser.error("--auto requires a feature run")
    if args.auto and args.no_worktree:
        parser.error("--auto requires an isolated worktree")
    if args.auto and args.resume_at != "generate":
        parser.error("--auto cannot resume at review")
    if args.report_only and not args.lint_fix:
        parser.error("--report-only only makes sense with --lint-fix")
    if args.lint_fix:
        _validate_lint_fix_flags(parser, args)
    else:
        _validate_build_flags(parser, args)


def _resolve_input_path(path: str | None, working_dir: Path) -> Path | None:
    if path is None:
        return None
    candidate = Path(path)
    return (candidate if candidate.is_absolute() else working_dir / candidate).resolve()


def parse_inputs(entries: list[str]) -> dict[str, str]:
    values = {}
    for entry in entries:
        name, separator, value = entry.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError("--input must be name=value with an identifier name")
        values[name] = value
    return values


def validate_workflow_args(args, working_dir):
    if args.command != "run" or args.lint_fix:
        return
    from meow.project.config import load_config
    from meow.project.custom.workflows import select_workflow

    select_workflow(load_config(working_dir), args.workflow)


def _validate_ci_flags(parser: argparse.ArgumentParser, args) -> None:
    if not args.ci:
        if args.target_ref or args.artifact_dir or args.post_comments:
            parser.error(
                "--target-ref, --artifact-dir and --post-comments require --ci"
            )
        return
    incompatible = [
        name
        for name, active in (
            ("prompt", args.request),
            ("--fix", args.fix),
            ("--jira", args.jira is not None),
            ("--gitlab", args.gitlab),
            ("--github", args.github),
            ("--branch", args.branch),
            ("--target", args.target),
            ("--review-file", args.review_file),
            ("--no-worktree", args.no_worktree),
            ("--test", args.test),
            ("--scope", args.scope != "auto"),
        )
        if active
    ]
    if incompatible:
        parser.error("--ci cannot be combined with " + ", ".join(incompatible))
