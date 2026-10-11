"""Role prompts, as pure functions of plain values.

Each role's words live in its agent definition file (the shipped ones under
`project/custom/builtin/agents/`, overridable per scope). The builders here
gather one invocation's values and render that definition, so the SDK agents
in `meow.agents` and `meow native prompt` get the same text and cannot drift
apart. Pass `agent` to render an override; without it the builtin definition
is used.

What stays here is code-owned: values computed from config (lint
instructions, shaping context) and the formats code parses (the verdict
contract, the MCP fetch contract).
"""

import json
from pathlib import Path

from meow.project.config_models import LintCommand
from meow.project.custom import CustomAgent
from meow.project.custom.rendering import builtin_agent, render_agent
from meow.project.custom.templates import render
from meow.project.shaping import ShapeContext


def _agent(agent: CustomAgent | None, name: str) -> CustomAgent:
    return agent if agent is not None else builtin_agent(name)


def shape_values(context: ShapeContext | None) -> dict | None:
    """The `shape` template variable for an accepted shaping context."""
    if context is None:
        return None
    return {
        "artifact_path": context.artifact_path,
        "chosen_approach": context.chosen_approach,
        "assumptions": ", ".join(context.assumptions) or "(none recorded)",
    }


def docs_update_prompt(
    baseline: str, head: str, *, agent: CustomAgent | None = None
) -> str:
    """Constrain the manual documentation role to evidence-backed prose edits."""
    return render_agent(
        _agent(agent, "docs-updater"), {"baseline": baseline, "head": head}
    )


def mcp_probe_prompt(label: str, token: str, probe: str) -> str:
    """Connectivity check for an MCP fetcher; the caller verifies a tool ran."""
    return (
        f"Call a tool from the configured {label} MCP server to confirm it is "
        f"reachable -- for example, {probe}. Reply with exactly '{token}_OK' if "
        f"a tool call succeeds, or '{token}_FAIL: <reason>' if it does not."
    )


def mcp_fetch_prompt(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] -- pure builder
    label: str,
    request: str,
    lookup: str,
    fields: str,
    output_file: Path,
    forbidden: str,
) -> str:
    """Fetch one record through an MCP server into a JSON file, read-only."""
    return (
        f"You fetch one {label} {request}'s details through the configured "
        f"{label} MCP server and write them to a file -- nothing else. {lookup} "
        f"Then write a JSON object with exactly the keys {fields} to "
        f"{output_file}. Do not write any other file, and do not modify the "
        f"{request}{forbidden}."
    )


def shape_context_instructions(context: ShapeContext | None) -> str:
    values = shape_values(context)
    if values is None:
        return ""
    return (
        f" Accepted shaping context: artifact path {values['artifact_path']}; "
        f"chosen approach {values['chosen_approach']}; assumptions: "
        f"{values['assumptions']}. Treat assumptions as Sprint Contract inputs "
        "and surface contradictions as findings."
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


def verdict_format(review_file: Path, unit: str) -> str:
    """The verdict-file contract `agents.reviewer._verdict_status` parses."""
    return (
        f"Write your verdict to {review_file} with the first line "
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


def _reviewer_fragment(name: str, agent: CustomAgent | None, **values: object) -> str:
    reviewer = _agent(agent, "reviewer")
    variables = {"check_worktree_hygiene": None, "has_diff": None, "docs_dir": None}
    variables.update(values)
    return render(
        "{{> " + name + "}}",
        variables,
        fragments=reviewer.fragments,
        where=str(reviewer.file),
    )


def architecture_review_instructions(
    *, check_worktree_hygiene: bool = True, agent: CustomAgent | None = None
) -> str:
    """Tell the reviewer to look for monolithic or overgrown modules."""
    return _reviewer_fragment(
        "architecture", agent, check_worktree_hygiene=check_worktree_hygiene
    )


def no_prompt_review_instructions(
    *, has_diff: bool, docs_dir: str, agent: CustomAgent | None = None
) -> str:
    """Select the review scope when the user did not supply a prompt."""
    return _reviewer_fragment("no_prompt", agent, has_diff=has_diff, docs_dir=docs_dir)


def tester_prompt(report_file: Path, *, agent: CustomAgent | None = None) -> str:
    """Instructions for the exploratory tester role and its private report."""
    return render_agent(_agent(agent, "tester"), {"report_file": report_file})


def explorer_prompt(active_dir: Path, *, agent: CustomAgent | None = None) -> str:
    return render_agent(_agent(agent, "explorer"), {"active_dir": active_dir})


def planner_prompt(  # ruff: ignore[too-many-arguments] -- pure builder mirroring the planner's inputs
    plan_file: Path,
    shape_context: ShapeContext | None = None,
    project_context: dict | None = None,
    *,
    bug_mode: bool = False,
    agent: CustomAgent | None = None,
) -> str:
    return render_agent(
        _agent(agent, "planner"),
        {
            "plan_file": plan_file,
            "shape": shape_values(shape_context),
            "preplan": (
                json.dumps(project_context, sort_keys=True) if project_context else None
            ),
            "bug_mode": bug_mode,
        },
    )


def generator_prompt(plan_file: Path, *, agent: CustomAgent | None = None) -> str:
    return render_agent(_agent(agent, "generator"), {"plan_file": plan_file})


def review_fixer_prompt(*, agent: CustomAgent | None = None) -> str:
    return render_agent(_agent(agent, "review-fixer"), {})


def lint_fixer_prompt(*, agent: CustomAgent | None = None) -> str:
    return render_agent(_agent(agent, "lint-fixer"), {})


def plan_review_prompt(  # ruff: ignore[too-many-arguments] -- pure builder mirroring the reviewer's inputs
    plan_file: Path,
    review_file: Path,
    lint_commands: list[LintCommand],
    *,
    focus: str | None,
    check_worktree_hygiene: bool,
    shape_context: ShapeContext | None = None,
    agent: CustomAgent | None = None,
) -> str:
    return render_agent(
        _agent(agent, "reviewer"),
        {
            "plan_file": plan_file,
            "review_file": review_file,
            "focus": focus,
            "lint_instructions": lint_instructions(lint_commands),
            "check_worktree_hygiene": check_worktree_hygiene,
            "verdict_format": verdict_format(review_file, "criterion"),
            "shape": shape_values(shape_context),
        },
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
    agent: CustomAgent | None = None,
) -> str:
    return render_agent(
        _agent(agent, "reviewer"),
        {
            "review_basis": review_basis,
            "review_file": review_file,
            "has_diff": has_diff,
            "docs_dir": docs_dir,
            "lint_instructions": lint_instructions(lint_commands),
            "check_worktree_hygiene": check_worktree_hygiene,
            "verdict_format": verdict_format(review_file, "requirement"),
            "shape": shape_values(shape_context),
        },
        variant="prompt",
    )


def remote_review_prompt(
    review_file: Path,
    request_label: str,
    *,
    check_worktree_hygiene: bool,
    agent: CustomAgent | None = None,
) -> str:
    return render_agent(
        _agent(agent, "reviewer"),
        {
            "review_file": review_file,
            "request_label": request_label,
            "check_worktree_hygiene": check_worktree_hygiene,
            "verdict_format": verdict_format(review_file, "concern"),
        },
        variant="remote",
    )


def branch_review_prompt(  # ruff: ignore[too-many-arguments] -- pure builder mirroring the reviewer's inputs
    target: str,
    branch: str,
    review_file: Path,
    lint_commands: list[LintCommand],
    *,
    check_worktree_hygiene: bool,
    shape_context: ShapeContext | None = None,
    agent: CustomAgent | None = None,
) -> str:
    return render_agent(
        _agent(agent, "reviewer"),
        {
            "target": target,
            "branch": branch,
            "review_file": review_file,
            "lint_instructions": lint_instructions(lint_commands),
            "check_worktree_hygiene": check_worktree_hygiene,
            "verdict_format": verdict_format(review_file, "concern"),
            "shape": shape_values(shape_context),
        },
        variant="branch",
    )


def ci_review_prompt(
    source_sha: str,
    target_sha: str,
    merge_base: str,
    *,
    agent: CustomAgent | None = None,
) -> str:
    return render_agent(
        _agent(agent, "reviewer"),
        {"source_sha": source_sha, "target_sha": target_sha, "merge_base": merge_base},
        variant="ci",
    )
