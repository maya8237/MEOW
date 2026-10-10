"""
meow/review_cli.py

`meow review`'s single dispatcher: replaces the top-level CLI-facing
functions that used to live in `review_runner.py` (the old `meow review`/
`meow cr`), `gitlab_reviewer.py` (the old `meow gitlab-review`),
`branch_reviewer.py` (the old `meow branch-review`), and
`review_fix_review.py` (the old `meow review-fix-review`)
-- one review-and-optionally-fix operation, sourced from a free-text prompt,
a Jira issue, a GitLab merge request, a GitHub pull request, a local branch's
diff against a target, an existing plan file (or the latest one
auto-discovered), or an existing review file being resumed.

The underlying mechanisms are unchanged and still exactly four: prompt-based
(`ReviewerAgent.review_prompt` + `ReviewFixAgent` via `_run_prompt_fix_rounds`),
plan-based (`ReviewerAgent.review_plan` + `Generator` via `_run_review_rounds`,
Sprint-Contract-aware), branch-based (`ReviewerAgent.review_branch` +
`ReviewFixAgent`, same loop as prompt-based but a diff-recomputing re-review),
and remote-based (`ReviewerAgent.review_remote_change`, always report-only --
no local checkout exists to fix). `--jira` and bare/auto-discovered sources
just pick which of the first two mechanisms to feed; `--gitlab`, `--github`,
and `--branch` select their corresponding source paths.
"""

from pathlib import Path

from meow.agents.base import ProjectContext
from meow.agents.reviewer import ReviewerAgent, _verdict_status
from meow.execution.orchestrator import (
    _run_prompt_fix_rounds,
    _run_review_rounds,
    review_then_test,
)
from meow.execution.run_state import RunStore
from meow.execution.sprint import build_sprint
from meow.infrastructure.lint import describe_lint_plan
from meow.infrastructure.logging import get_logger
from meow.infrastructure.worktree import (
    _ensure_existing_branch_worktree,
    _require_branch_checked_out,
    current_branch,
    sanitize_name,
)
from meow.integrations.github_reviewer import _fetch_pull_request, _load_github_config
from meow.integrations.gitlab_reviewer import _fetch_merge_request, _load_gitlab_config
from meow.integrations.issue_solver import _fetch_issue, _load_jira_config
from meow.project.config import load_config
from meow.project.onboarding import onboard_if_needed
from meow.project.plan_files import _detect_review_flavor, _latest_plan_file

logger = get_logger(__name__)


def _review_sources(  # ruff: ignore[too-many-arguments]
    jira_key: str | None,
    gitlab_link: str | None,
    branch: str | None,
    plan_file: Path | None,
    *,
    github_link: str | None = None,
) -> list[str]:
    sources = []
    if jira_key is not None:
        sources.append("--jira")
    if gitlab_link is not None:
        sources.append("--gitlab")
    if github_link is not None:
        sources.append("--github")
    if branch is not None:
        sources.append("--branch")
    if plan_file is not None:
        sources.append("--plan")
    return sources


def _validate_review_flags(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] -- one flag per named review source, mirrors the CLI one to one
    prompt: str | None,
    jira_key: str | None,
    gitlab_link: str | None,
    branch: str | None,
    target: str | None,
    plan_file: Path | None,
    review_file: Path | None,
    *,
    github_link: str | None = None,
    test: bool = False,
) -> None:
    """Enforce "at most one review source", with `--review-file` (resuming
    an existing verdict) as the one case where `prompt` is a modifier
    (supplementary focus text) rather than a competing source -- exactly
    today's `review-fix-review <prompt> --review-file <path>` shape."""
    if (branch is None) != (target is None):
        raise ValueError("--branch and --target must be given together")

    other_sources = _review_sources(
        jira_key,
        gitlab_link,
        branch,
        plan_file,
        github_link=github_link,
    )
    if len(other_sources) > 1:
        joined = ", ".join(other_sources)
        raise ValueError(f"Give at most one review source, got: {joined}")
    if other_sources and review_file is not None:
        raise ValueError(f"--review-file can't be combined with {other_sources[0]}")
    if prompt and other_sources:
        raise ValueError(f"Give either a prompt or {other_sources[0]}, not both")
    test_has_other_source = any((
        prompt,
        jira_key,
        gitlab_link,
        github_link,
        branch,
        review_file,
    ))
    if test and (plan_file is None or test_has_other_source):
        raise ValueError(
            "--test requires an explicit --plan and cannot be used "
            "with other review sources"
        )


def _raise_if_not_passed(passed: bool, config: dict, label: str) -> None:
    if passed:
        logger.info("review_complete", label=label)
        return
    logger.error("review_did_not_pass", label=label, max_rounds=config["max_rounds"])
    raise RuntimeError(
        f"{label} did not pass after {config['max_rounds']} rounds -- "
        "stopping instead of looping forever. Inspect the review file."
    )


async def _prompt_review(
    working_dir: Path, config: dict, prompt: str | None, *, fix: bool
) -> None:
    context = ProjectContext(working_dir, config)
    status, verdict = await ReviewerAgent(context).review_prompt(prompt)
    if not fix:
        logger.info("prompt_review_finished", status=status)
        return
    passed = await _run_prompt_fix_rounds(
        context,
        (status, verdict),
        re_review=lambda: ReviewerAgent(context).review_prompt(prompt),
    )
    _raise_if_not_passed(passed, config, "review-fix of the prompt")


async def _plan_review(  # ruff: ignore[too-many-arguments]
    working_dir: Path, config: dict, plan_file: Path, *, fix: bool, test: bool = False
) -> None:
    sprint = build_sprint(working_dir, config)
    if not fix:
        if test:
            result = await review_then_test(sprint, plan_file, 1)
            logger.info(
                "plan_review_finished",
                plan_file=str(plan_file),
                status=result.reviewer_status,
                tester_status=result.tester_status,
            )
        else:
            status, _ = await ReviewerAgent(sprint).review_plan(plan_file)
            logger.info("plan_review_finished", plan_file=str(plan_file), status=status)
        return
    passed = await _run_review_rounds(sprint, plan_file, test=test)
    _raise_if_not_passed(passed, config, f"Review of {plan_file}")


async def _plan_or_prompt_review(  # ruff: ignore[too-many-arguments] -- pass-through of the CLI's own review-mode flags
    working_dir: Path,
    config: dict,
    prompt: str | None,
    plan_file: Path | None,
    *,
    fix: bool,
    test: bool = False,
) -> None:
    """No explicit source given: auto-discover the latest plan (today's
    `meow review` default); if none exists, fall back to a prompt-based
    review of the git diff/whole project (today's bare `meow cr` default).
    An explicitly given `plan_file` is used as-is and fails loudly if it
    doesn't exist -- only the "nothing given" case falls back."""
    resolved_plan_file = plan_file
    if resolved_plan_file is None and not prompt:
        try:
            resolved_plan_file = _latest_plan_file(working_dir / config["docs_dir"])
        except FileNotFoundError:
            resolved_plan_file = None

    if resolved_plan_file is not None:
        await _plan_review(working_dir, config, resolved_plan_file, fix=fix, test=test)
    else:
        await _prompt_review(working_dir, config, prompt, fix=fix)


async def _branch_review(  # ruff: ignore[too-many-arguments] -- mirrors the CLI's own --branch flags one to one
    working_dir: Path,
    config: dict,
    branch: str,
    target: str,
    *,
    fix: bool,
    use_worktree: bool,
) -> None:
    if use_worktree:
        feature_name = f"branch-review-{sanitize_name(branch)}"
        active_dir = _ensure_existing_branch_worktree(working_dir, feature_name, branch)
    else:
        active_dir = working_dir
        _require_branch_checked_out(active_dir, branch)

    journal = config.get("_run_journal")
    if journal:
        store, run_id = journal
        store.transition(run_id, "reviewing", worktree=str(active_dir), branch=branch)

    context = ProjectContext(active_dir, config, use_worktree=use_worktree)
    status, verdict = await ReviewerAgent(context).review_branch(target, branch)
    if not fix:
        logger.info("branch_review_finished", branch=branch, status=status)
        return
    passed = await _run_prompt_fix_rounds(
        context,
        (status, verdict),
        re_review=lambda: ReviewerAgent(context).review_branch(target, branch),
    )
    label = f"branch-review of {branch!r} against {target!r}"
    _raise_if_not_passed(passed, config, label)


async def _gitlab_review(working_dir: Path, config: dict, gitlab_link: str) -> None:
    gitlab_config = _load_gitlab_config(config)
    context = ProjectContext(working_dir, config)
    mr = await _fetch_merge_request(working_dir, config, gitlab_config, gitlab_link)
    status, _ = await ReviewerAgent(context).review_remote_change(
        mr["title"], mr["description"], mr["diff"], provider="gitlab"
    )
    logger.info("gitlab_review_finished", status=status)


async def _github_review(working_dir: Path, config: dict, github_link: str) -> None:
    github_config = _load_github_config(config)
    context = ProjectContext(working_dir, config)
    pr = await _fetch_pull_request(working_dir, config, github_config, github_link)
    status, _ = await ReviewerAgent(context).review_remote_change(
        pr["title"], pr["description"], pr["diff"], provider="github"
    )
    logger.info("github_review_finished", status=status)


async def _jira_review_prompt(working_dir: Path, config: dict, jira_key: str) -> str:
    jira_config = _load_jira_config(config)
    issue = await _fetch_issue(working_dir, config, jira_config, jira_key or None)
    return (
        f"Review whether the current code satisfies Jira issue {issue['key']}: "
        f"{issue['summary']}\n\n{issue['description']}"
    )


_UNRESUMABLE_FLAVOR_MESSAGES = {
    "gitlab": (
        "{file} is a GitLab merge request review -- `meow review` "
        "can't resume it: there is no local checkout of the merge "
        "request's code, and --gitlab never creates one. Check out the "
        "MR's branch locally and review that checkout instead, or "
        "address the MR feedback directly."
    ),
    "github": (
        "{file} is a GitHub pull request review -- `meow review` "
        "can't resume it: there is no local checkout of the pull "
        "request's code, and --github never creates one. Check out the "
        "PR's branch locally and review that checkout instead, or "
        "address the PR feedback directly."
    ),
    "branch": (
        "{file} is a branch review -- `meow review` can't resume "
        "it from --review-file alone (it doesn't know the target branch "
        "to re-diff against). Run `meow review --fix --branch "
        "<branch> --target <target>` again instead; it already loops "
        "review, fix, and re-review until it passes."
    ),
}


async def _resume_review_file(
    working_dir: Path, config: dict, prompt: str | None, review_file: Path
) -> None:
    flavor = _detect_review_flavor(review_file)
    if flavor in _UNRESUMABLE_FLAVOR_MESSAGES:
        message = _UNRESUMABLE_FLAVOR_MESSAGES[flavor].format(file=review_file)
        raise RuntimeError(message)

    review_text = review_file.read_text(encoding="utf-8")
    initial_verdict = (_verdict_status(review_text), review_text)

    if flavor == "plan":
        plan_file = review_file.with_name(
            review_file.name.removesuffix("-review.md") + ".md"
        )
        if not plan_file.exists():
            raise FileNotFoundError(
                f"{review_file} looks like a plan review, but its plan "
                f"file {plan_file} no longer exists."
            )
        sprint = build_sprint(working_dir, config)
        passed = await _run_review_rounds(
            sprint, plan_file, initial_verdict=initial_verdict, focus=prompt
        )
    else:
        context = ProjectContext(working_dir, config)
        passed = await _run_prompt_fix_rounds(
            context,
            initial_verdict,
            re_review=lambda: ReviewerAgent(context).review_prompt(prompt),
        )

    _raise_if_not_passed(passed, config, f"review-fix-review of {review_file}")


async def run_review_command(  # ruff: ignore[too-many-arguments, too-many-statements, complex-structure, too-many-branches] -- one flag per named review source, mirrors the CLI one to one
    working_dir: Path,
    prompt: str | None,
    *,
    fix: bool,
    jira_key: str | None = None,
    gitlab_link: str | None = None,
    github_link: str | None = None,
    branch: str | None = None,
    target: str | None = None,
    plan_file: Path | None = None,
    review_file: Path | None = None,
    use_worktree: bool = True,
    test: bool = False,
) -> None:
    """`meow review`'s dispatcher. Exactly one of `jira_key`,
    `gitlab_link`, `github_link`, `branch`(+`target`), `plan_file`, or
    `review_file` may be
    given; none given falls back to `prompt` (or full auto-discovery if
    `prompt` is also `None` -- see `_plan_or_prompt_review`). `fix` toggles
    a single report-only pass vs. looping review-fix-review to
    `max_rounds` (raising if it never passes) -- except `review_file`,
    which always implies fixing (resuming a review file only makes sense
    if something is going to act on it), and remote links, which reject
    `fix=True` outright: there is no local checkout of a remote request to
    fix.
    """
    _validate_review_flags(
        prompt,
        jira_key,
        gitlab_link,
        branch,
        target,
        plan_file,
        review_file,
        github_link=github_link,
        test=test,
    )
    remote_flag = next(
        (
            flag
            for flag, link in (("--gitlab", gitlab_link), ("--github", github_link))
            if link is not None
        ),
        None,
    )
    if remote_flag and fix:
        raise ValueError(
            f"{remote_flag} can't be combined with --fix -- there is no local "
            "checkout of a remote request to fix."
        )

    store = RunStore(working_dir) if working_dir.is_dir() else None
    record = None
    if store:
        request = prompt or jira_key or gitlab_link or github_link or branch
        request = request or str(plan_file or review_file or "latest")
        record = store.create(
            source="review",
            request=request,
            repo=working_dir,
            worktree=working_dir,
            branch=current_branch(working_dir),
        )
        store.transition(
            record.id,
            "reviewing",
            plan_file=str(plan_file) if plan_file else None,
        )
    try:
        onboard_if_needed(working_dir, working_dir)
        config = load_config(working_dir)
        # Keep lightweight in-memory configs accepted by integrations and tests.
        config.setdefault("docs_dir", "docs")
        config.setdefault("max_rounds", 3)
        if store and record:
            config["_run_journal"] = (store, record.id)
        describe_lint_plan(config["lint"])

        if review_file is not None:
            await _resume_review_file(working_dir, config, prompt, review_file)
        elif gitlab_link is not None:
            await _gitlab_review(working_dir, config, gitlab_link)
        elif github_link is not None:
            await _github_review(working_dir, config, github_link)
        elif branch is not None:
            await _branch_review(
                working_dir, config, branch, target, fix=fix, use_worktree=use_worktree
            )
        elif jira_key is not None:
            jira_prompt = await _jira_review_prompt(working_dir, config, jira_key)
            await _prompt_review(working_dir, config, jira_prompt, fix=fix)
        else:
            await _plan_or_prompt_review(
                working_dir,
                config,
                prompt,
                plan_file,
                fix=fix,
                **({"test": True} if test else {}),
            )
    except BaseException as exc:
        if store and record:
            store.transition(record.id, "failed", last_failure=str(exc))
        raise
    if store and record:
        store.transition(record.id, "review_finished")
