"""Command dispatch: turns parsed, validated arguments into a call to the
matching workflow entry point."""

import asyncio
import json
import sys
from pathlib import Path

from meow.cli.queue_cli import queue
from meow.cli.review_cli import run_review_command
from meow.cli.validation import _resolve_input_path
from meow.execution.orchestrator import PlanNotApprovedError
from meow.execution.plan_approval import _prompt_plan_approval
from meow.execution.sprint_runner import run_plan, run_sprint
from meow.hooks.claude import (
    inspect_claude_hooks,
    install_claude_hooks,
    uninstall_claude_hooks,
)
from meow.infrastructure.lint_fix import run_lint_fix
from meow.integrations.issue_solver import IssueUnresolvedError, run_issue_solver


def _dispatch_lint_fix(args, working_dir: Path) -> None:
    report = asyncio.run(run_lint_fix(working_dir, report_only=args.report_only))
    if args.report_only:
        print(report if report else "Lint is clean -- no issues found.")


def _dispatch_jira_build(args, working_dir: Path) -> None:
    approve_plan = _prompt_plan_approval if args.manually_approve_plan else None
    try:
        result = asyncio.run(
            run_issue_solver(
                working_dir,
                args.jira or None,
                approve_plan=approve_plan,
                **({"test": True} if args.test else {}),
            )
        )
    except IssueUnresolvedError as exc:
        print(f"\nWARNING: could not resolve the issue: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(result))


def _dispatch_plain_build(args, working_dir: Path, *, use_worktree: bool) -> None:
    plan_file = _resolve_input_path(args.plan, working_dir)
    approve_plan = _prompt_plan_approval if args.manually_approve_plan else None
    try:
        asyncio.run(
            run_sprint(
                working_dir,
                args.feature_name,
                args.request,
                use_worktree=use_worktree,
                plan_file=plan_file,
                source_branch=args.source_branch,
                approve_plan=approve_plan,
                resume_at=args.resume_at,
                **({"unattended": True} if args.unattended else {}),
                **({"test": True} if args.test else {}),
            )
        )
    except PlanNotApprovedError as exc:
        print(f"\n{exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def _dispatch_run(args, working_dir: Path, *, use_worktree: bool) -> None:
    if args.lint_fix:
        _dispatch_lint_fix(args, working_dir)
    elif args.jira is not None:
        _dispatch_jira_build(args, working_dir)
    else:
        _dispatch_plain_build(args, working_dir, use_worktree=use_worktree)


def _dispatch_review(args, working_dir: Path) -> None:
    plan_file = _resolve_input_path(args.plan, working_dir)
    review_file = _resolve_input_path(args.review_file, working_dir)
    try:
        review_kwargs = {
            "fix": args.fix,
            "jira_key": args.jira,
            "gitlab_link": args.gitlab,
            "branch": args.branch,
            "target": args.target,
            "plan_file": plan_file,
            "review_file": review_file,
            "use_worktree": not args.no_worktree,
        }
        if args.github is not None:
            review_kwargs["github_link"] = args.github
        if args.test:
            review_kwargs["test"] = True
        asyncio.run(run_review_command(working_dir, args.request, **review_kwargs))
    except ValueError as exc:
        print(f"\n{exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def _dispatch(args, working_dir: Path, *, use_worktree: bool) -> None:
    if args.command == "hooks":
        if args.host != "claude":
            raise ValueError("unsupported host")
        if args.hooks_command == "install":
            selected = args.only or ["lint", "shaping", "plan_capture", "plan_stop"]
            print(
                json.dumps(
                    install_claude_hooks(working_dir, selected, dry_run=args.dry_run)
                )
            )
        elif args.hooks_command == "status":
            print(json.dumps(inspect_claude_hooks(working_dir)))
        else:
            print(json.dumps(uninstall_claude_hooks(working_dir)))
        return
    if args.command == "run":
        _dispatch_run(args, working_dir, use_worktree=use_worktree)
        return
    if args.command == "review":
        _dispatch_review(args, working_dir)
        return
    if args.command == "queue":
        raise SystemExit(queue(working_dir, args.request, args.feature_name))
    plan_file = asyncio.run(
        run_plan(
            working_dir,
            args.feature_name,
            args.request,
            use_worktree=use_worktree,
            source_branch=args.source_branch,
        )
    )
    # The name may differ from --name when that worktree was already taken.
    print(f"Plan written: {plan_file}")
