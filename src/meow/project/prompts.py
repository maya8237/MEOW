"""Role prompts, as pure functions of plain values.

Both execution modes build their prompts here: the SDK agents in
`meow.agents` pass them to `ClaudeAgentOptions`, and `meow native prompt`
prints them for a Claude Code session to hand to a Task subagent. Keeping
one copy means the two modes cannot drift apart. Nothing here touches the
filesystem, the SDK, or a Sprint.
"""

import json
from pathlib import Path

from meow.project.config import LintCommand
from meow.project.shaping import ShapeContext


def docs_update_prompt(baseline: str, head: str) -> str:
    """Constrain the manual documentation role to evidence-backed prose edits."""
    return (
        "You maintain project documentation for a manually requested update. "
        f"Compare Git commits {baseline}..{head} using the supplied diff and "
        "read code, tests, and existing docs to verify each factual statement. "
        "Edit only README.md or prose files under docs/ (.md, .rst, .txt). "
        "Never edit source, tests, configuration, AGENTS.md, or the "
        "docs/.meow-docs-update.json marker. Do not create a document merely "
        "to fill a template. Make only relevant updates supported by observed "
        "behavior. If evidence is insufficient, leave the docs unchanged and "
        "report uncertainty. Do not invent features or recommendations."
    )


def shape_context_instructions(context: ShapeContext | None) -> str:
    if context is None:
        return ""
    assumptions = ", ".join(context.assumptions) or "(none recorded)"
    return (
        " Accepted shaping context: artifact path "
        + context.artifact_path
        + "; chosen approach "
        + context.chosen_approach
        + "; assumptions: "
        + assumptions
        + ". Treat assumptions as Sprint Contract inputs and surface "
        "contradictions as findings."
    )


def lint_instructions(commands: list[LintCommand]) -> str:
    """Tell the reviewer which lint commands bind it and which only inform."""
    gates = [entry.command for entry in commands if entry.gate]
    non_blocking = [entry.command for entry in commands if not entry.gate]
    parts = []
    if gates:
        listed = ", ".join(f"`{command}`" for command in gates)
        parts.append(
            "Meow runs these project-wide before review and provides the "
            "observed output below. Do not run them a second time. Treat "
            f"any listed failure as a FAIL criterion: {listed}."
        )
    if non_blocking:
        listed = ", ".join(f"`{command}`" for command in non_blocking)
        parts.append(
            f"Meow also runs {listed} and provides its observed output. "
            "Summarise advisory findings, but do not fail the sprint on them."
        )
    return " ".join(parts)


def verification_instructions() -> str:
    """Require independent, evidence-based verdicts without editing code."""
    return (
        "Use verification-before-completion: base every PASS or FAIL on "
        "inspected code or observed command output; do not modify implementation. "
    )


def tester_prompt(report_file: Path) -> str:
    """Instructions for the exploratory tester role and its private report."""
    return (
        "You are an exploratory software tester. Inspect the supplied plan, "
        "architecture context, test command evidence, test directories, and "
        "available base URLs. Investigate the implementation and run relevant "
        "tests or manual checks with Bash when useful. Do not edit implementation "
        "or test files. Write your verdict only to "
        f"{report_file}: begin with `SUMMARY: <observed result>` and then "
        "`STATUS: PASS` or `STATUS: FAIL`. Base the verdict on observed behavior "
        "and include concise reproduction steps and command results where useful. "
        "Do not report configured command failures as passing."
    )


def architecture_review_instructions(*, check_worktree_hygiene: bool = True) -> str:
    """Tell the reviewer to look for monolithic or overgrown modules."""
    instructions = (
        "For the changed behavior, identify the public seam and module boundary "
        "that should own it. Prefer a deep, cohesive module with a narrow "
        "interface over spreading policy across callers. "
        "Also perform a SOLID/SRP review. Flag any file or class that mixes "
        "multiple responsibilities, such as config parsing + agent wiring + "
        "lint hooks + orchestration + CLI handling in one module. Treat any "
        "single file that does more than one broad concern as a FAIL criterion "
        "unless the code is clearly split into cohesive helpers or classes. "
        "Review module boundaries in any language as well: inspect packages, "
        "directories, and namespaces for clusters of too many unrelated files "
        "or one module that has become a catch-all for several independent "
        "concerns. A large module is not automatically a failure; use cohesion, "
        "dependency direction, discoverability, and change patterns as evidence. "
        "When the concentration materially harms those qualities, report the "
        "specific files and propose a concrete responsibility-based split. Treat "
        "that as a FAIL criterion only when the boundary problem affects the "
        "changed feature or clearly makes maintenance unsafe; otherwise record "
        "it as an advisory follow-up. Do not demand arbitrary file-count or line "
        "limits, and do not split merely to make files smaller. "
    )
    if check_worktree_hygiene:
        instructions += (
            "If the sprint used an isolated worktree, ensure every plan change "
            "stays inside the active worktree and that the main working "
            "directory remains clean; any edit there is a FAIL criterion. "
        )
    instructions += (
        "Use file:line evidence; do not accept 'it works' as an excuse for a "
        "monolithic design."
    )
    return instructions


def no_prompt_review_instructions(*, has_diff: bool, docs_dir: str) -> str:
    """Select the review scope when the user did not supply a prompt."""
    exclusion = (
        f" Do not review anything under {docs_dir!r} -- that directory holds "
        "meow's own generated sprint plans and review verdicts, not project "
        "code, and grading them as if they were the project under review "
        "produces nonsensical meta-reviews."
    )
    if has_diff:
        return (
            "There is no explicit prompt and the git diff contains changes. "
            "Review the changes shown in the git diff, using git status for "
            "context." + exclusion
        )
    return (
        "There is no explicit prompt and the git diff is empty. Review the "
        "entire project by inspecting its source and configuration files, "
        "looking for correctness issues and incomplete or broken behavior." + exclusion
    )


def explorer_prompt(active_dir: Path) -> str:
    return (
        "You are a read-only research agent. Investigate the question "
        "you're given within this project directory: "
        f"{active_dir}. "
        "Treat it as the project root and resolve relative paths from "
        "it. Then return only a concise summary with "
        "file:line references -- never dump raw file contents or full "
        "command output unless specifically asked to. When the task is "
        "about a bug, use systematic debugging to gather evidence and "
        "trace likely causes; stay read-only and report findings."
    )


def planner_prompt(
    plan_file: Path,
    shape_context: ShapeContext | None = None,
    project_context: dict | None = None,
    *,
    bug_mode: bool = False,
) -> str:
    bug_instructions = (
        " This is a bug-fix request. The plan must first reproduce the failure "
        "with a tight, red-capable test at the real public seam, then minimise it, "
        "rank falsifiable hypotheses, and define the regression test and "
        "verification evidence that will prove the fix. Do not plan speculative "
        "refactoring before the cause is established."
        if bug_mode
        else ""
    )
    return (
        "You are a planning agent. Consult the explorer subagent for "
        "any codebase context you need -- don't explore directly. "
        "Produce a numbered task list with acceptance criteria per "
        "task, plus a proposed '## Sprint Contract' section with "
        f"concrete, testable pass/fail criteria. Write the result to "
        f"{plan_file}. Do not write application code. Apply the "
        "writing-plans skill for task sizing, file mapping, testability, "
        "and self-review. MEOW-specific requirements take precedence: "
        "use the configured plan path, retain this Sprint Contract, "
        "and do not use the skill's default plan location or add an "
        "interactive execution-method handoff."
        f" For substantial work with independent tasks and disjoint file ownership, "
        f"write {plan_file}.tasks.json as JSON with a tasks array. Each task "
        "has id, depends_on, owned_paths, and verification fields. "
        "For a request containing multiple distinct feature, bug fix, or validation "
        "outcomes, split the plan into one independently verifiable task per outcome "
        "before mapping implementation steps. Use the task graph for every such "
        "multi-outcome request when ownership can be made clear; do not collapse "
        "unrelated work into one oversized task. Set depends_on when a task relies "
        "on another task's API, schema, migration, regression fix, or verification. "
        "Treat shared files, shared public seams, and shared state as sequencing "
        "constraints rather than pretending those tasks are independent. Tasks with "
        "disjoint owned_paths and no dependency may be marked for parallel execution; "
        "otherwise keep them in dependency order. Omit this graph only for a simple "
        "single-outcome request or when ownership is genuinely unclear, and explain "
        "that choice in the plan."
        + shape_context_instructions(shape_context)
        + (
            " Pre-plan repository evidence: "
            + json.dumps(project_context, sort_keys=True)
            if project_context
            else ""
        )
        + " Identify the public seam being changed, the module boundary that owns "
        "it, and why the proposed placement preserves a deep, cohesive interface. "
        "Use cited observations in planning. Do not suggest doc edits."
        + bug_instructions
    )


def generator_prompt(plan_file: Path) -> str:
    return (
        f"You implement tasks from {plan_file} one at a time. "
        "Work against the agreed Sprint Contract criteria exactly "
        "-- do not expand scope. When you believe a task is "
        "complete, say so explicitly and stop; do not grade your "
        "own work. Use executing-plans to work through the supplied "
        "plan one task at a time and check each expected result. If the companion "
        f"{plan_file}.tasks.json exists, follow its dependency order and owned-path "
        "boundaries exactly: complete prerequisites first, keep independent tasks "
        "isolated, and never edit another task's owned paths without recording why "
        "the dependency requires it. The orchestrator may run disjoint graph tasks "
        "in parallel; treat each assigned slice as a separate deliverable and do not "
        "implement the rest of the graph. Use Agent subagents for read-only "
        "exploration or narrowly isolated analysis when that reduces context load; "
        "do not delegate overlapping edits or let a subagent expand scope. "
        "MEOW's orchestrator owns worktree setup, review rounds, and "
        "stopping control; do not create a separate ledger or commits. "
        "Use test-driven development for code changes: write a failing "
        "test, confirm the expected failure, implement, then run it "
        "and confirm it passes. When an unexpected failure occurs, "
        "use systematic debugging to establish its root cause before "
        "editing. For a bug-fix plan, first reproduce and minimise the "
        "reported failure, test ranked hypotheses one at a time, and leave "
        "a regression test at the correct public seam. Before acting on reviewer "
        "feedback, verify each "
        "finding against the code and plan; report unsupported or "
        "out-of-scope findings instead of making unrelated changes. "
        "Before reporting a task complete, run relevant project tests "
        "and report actual command results. Do not declare sprint "
        "success; the reviewer decides."
    )


def review_fixer_prompt() -> str:
    return (
        "You fix the review findings reported in the task message "
        "-- nothing else. Do not expand scope, refactor unrelated "
        "code, or change behavior beyond what each finding needs; "
        "make the smallest edit that resolves each one. When you "
        "believe every reported finding is fixed, say so "
        "explicitly and stop."
    )


def lint_fixer_prompt() -> str:
    return (
        "You fix lint failures reported in the task message -- "
        "nothing else. Do not expand scope, refactor unrelated "
        "code, or change behavior beyond what each failure needs; "
        "make the smallest edit that resolves each one. When you "
        "believe every reported failure is fixed, say so "
        "explicitly and stop."
    )


def _verdict_format(review_file: Path, unit: str) -> str:
    return (
        f" Write your verdict to {review_file} with the first line "
        "starting with 'SUMMARY:' and containing a brief one- or two-"
        "sentence summary. The next line must start with 'STATUS: PASS' "
        f"or 'STATUS: FAIL', followed by one line per {unit}. "
        "For a concrete maintenance issue relevant to the changed code, "
        "you may add one QUALITY_CONCERN: JSON object per issue with keys "
        "path, evidence, impact, and follow_up. Evidence must be exact code text "
        "currently present at that repository path. These concerns are advisory; "
        "do not change PASS to FAIL for a concern alone. "
        "Do not emit doc-update advice. "
        "Default to FAIL when uncertain."
    )


def plan_review_prompt(  # ruff: ignore[too-many-arguments] -- pure builder mirroring the reviewer's inputs
    plan_file: Path,
    review_file: Path,
    lint_commands: list[LintCommand],
    *,
    focus: str | None,
    check_worktree_hygiene: bool,
    shape_context: ShapeContext | None = None,
) -> str:
    focus_instruction = f" Pay particular attention to: {focus}." if focus else ""
    return (
        "You are a skeptical QA reviewer. You did not write this code "
        f"-- grade it critically. Read the Sprint Contract in {plan_file}. "
        "Check each criterion against the actual code and mark PASS or "
        "FAIL with concrete evidence (file:line or command output)."
        + focus_instruction
        + " "
        + lint_instructions(lint_commands)
        + " "
        + verification_instructions()
        + architecture_review_instructions(
            check_worktree_hygiene=check_worktree_hygiene
        )
        + _verdict_format(review_file, "criterion")
        + shape_context_instructions(shape_context)
    )


def prompt_review_prompt(  # ruff: ignore[too-many-arguments] -- pure builder mirroring the reviewer's inputs
    review_basis: str,
    review_file: Path,
    lint_commands: list[LintCommand],
    *,
    has_diff: bool,
    docs_dir: str,
    check_worktree_hygiene: bool,
    shape_context: ShapeContext | None = None,
) -> str:
    prompt_text = (
        f"The feature is described by this prompt: {review_basis!r}. "
        if review_basis
        else no_prompt_review_instructions(has_diff=has_diff, docs_dir=docs_dir) + " "
    )
    scope_instruction = (
        "Run `git status` and `git diff` in the working directory to "
        "confirm the review scope. "
        if not review_basis
        else "Check whether the current changes satisfy each distinct "
        "requirement implied by the task. "
    )
    return (
        "You are a skeptical QA reviewer. You did not write this code "
        "-- grade it critically. There is no Sprint Contract for this "
        "review; evaluate the current working tree instead. "
        + prompt_text
        + scope_instruction
        + "Use the working-tree context provided in the task message "
        "(git status and git diff output) as the source of truth. "
        "Mark each requirement PASS or FAIL with concrete evidence "
        "(file:line or command output). "
        + lint_instructions(lint_commands)
        + " "
        + verification_instructions()
        + architecture_review_instructions(
            check_worktree_hygiene=check_worktree_hygiene
        )
        + _verdict_format(review_file, "requirement")
        + shape_context_instructions(shape_context)
    )


def remote_review_prompt(
    review_file: Path, request_label: str, *, check_worktree_hygiene: bool
) -> str:
    return (
        "You are a skeptical QA reviewer. You did not write this "
        f"code -- grade it critically. You are reviewing a {request_label}'s "
        "diff, not a local working tree -- there is "
        "no Sprint Contract and no `git diff` to run yourself. Use "
        "only the remote request title, description, and diff given "
        "in the task message as your source of truth; Read/Grep/Glob "
        "the local project only for background context on the files "
        "the diff touches, if that helps. Do not run or reference "
        "the project's lint commands -- the local checkout may not "
        "be at the merge request's commit, so their result would "
        "not reflect this diff. Mark each distinct concern PASS or "
        "FAIL with concrete evidence (a quoted diff hunk or "
        "file:line). "
        + verification_instructions()
        + architecture_review_instructions(
            check_worktree_hygiene=check_worktree_hygiene
        )
        + _verdict_format(review_file, "concern")
    )


def branch_review_prompt(  # ruff: ignore[too-many-arguments] -- pure builder mirroring the reviewer's inputs
    target: str,
    branch: str,
    review_file: Path,
    lint_commands: list[LintCommand],
    *,
    check_worktree_hygiene: bool,
    shape_context: ShapeContext | None = None,
) -> str:
    return (
        "You are a skeptical QA reviewer. You did not write this code "
        f"-- grade it critically. You are reviewing local branch {branch!r} "
        f"against its target branch {target!r}. There is no Sprint "
        "Contract; the diff given in the task message (between "
        f"{target!r} and {branch!r}) sets the scope, but unlike a GitLab "
        "merge request review, this branch is actually checked out here "
        "-- use Read/Grep/Glob/Bash to inspect the real code and run the "
        "project's own checks, not just the diff text. Mark each distinct "
        "concern PASS or FAIL with concrete evidence (a quoted diff hunk "
        "or file:line). "
        + lint_instructions(lint_commands)
        + " "
        + verification_instructions()
        + architecture_review_instructions(
            check_worktree_hygiene=check_worktree_hygiene
        )
        + _verdict_format(review_file, "concern")
        + shape_context_instructions(shape_context)
    )


def ci_review_prompt(source_sha: str, target_sha: str, merge_base: str) -> str:
    return (
        "You are a skeptical, read-only code reviewer. Review only the supplied "
        f"diff from merge base {merge_base} to source commit {source_sha}; "
        f"the target commit is {target_sha}. The revisions are immutable. "
        "Use Read, Grep, and Glob only for context. Do not write files, run "
        "commands, invoke other tools, or propose a fix as a substitute for "
        "review. Return a concise report with file:line evidence. Include "
        "exactly one standalone line STATUS: PASS or STATUS: FAIL. "
        "Return FAIL if any material concern remains."
    )
