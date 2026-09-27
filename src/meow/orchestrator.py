"""
meow/orchestrator.py

The generic harness engine: explorer, planner, generator, and evaluator as
real peer agents, coordinated by plain Python control flow. Unlike the
single-project version, all project-specific values (lint commands, models,
round cap) are read from a `.harness.toml` file in the target project's
root, not hardcoded here -- this file is meant to be installed once and
reused across projects.

A project may configure any number of lint commands. Each one declares
whether it runs per edited file, whether it can auto-fix, and whether its
failure is allowed to fail a sprint -- see `_normalize_lint_commands`.

Install (from the harness repo root):    pip install -e .
Run (from inside a project repo):        harness run "Add CSV export"
"""

import argparse
import asyncio
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # Python 3.10 fallback -- pip install tomli

from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    TextBlock,
    query,
)

CONFIG_FILENAME = ".harness.toml"
DEFAULT_FIX_FLAG = "--fix"
LINT_ENTRY_KEYS = frozenset({"command", "fix_flag", "per_file", "gate"})

DEFAULT_CONFIG = {
    "lint_command": None,           # legacy single-command form
    "lint_fix_flag": DEFAULT_FIX_FLAG,
    "max_rounds": 8,
    "docs_dir": "docs/exec-plans/active",
    "models": {
        "explorer": "haiku",
        "planner": None,            # None = engine default
        "generator": None,
        "evaluator": None,
    },
}


# ---------------------------------------------------------------------------
# Lint commands
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LintCommand:
    """One lint command a project has configured.

    A `per_file` command runs as the generator's post-edit hook, on the single
    file just written. A `gate` command runs project-wide for the evaluator and
    its failure is a sprint FAIL; a non-gate command is advisory, which is what
    a whole-project analyzer needs when it is expected to report findings on
    pre-existing code.
    """

    command: str
    fix_flag: str | None = None
    per_file: bool = True
    gate: bool = True

    def argv(self) -> list[str]:
        """The command as argv, check-only, program resolved on PATH."""
        argv = self.command.split()
        if not argv:
            raise ValueError(f"{CONFIG_FILENAME}: lint command is empty.")
        # Windows will not exec a .cmd shim (npx, eslint and oxlint all ship
        # as one) from a bare argv, so look the program up the way a shell
        # would. Falls back to the original name so a genuinely missing
        # program still surfaces as the OS error rather than being hidden.
        return [shutil.which(argv[0]) or argv[0], *argv[1:]]

    def argv_for_file(self, file_path: str) -> list[str]:
        """The command as argv for one file, auto-fixing where supported."""
        argv = self.argv()
        if self.fix_flag:
            argv.append(self.fix_flag)
        argv.append(file_path)
        return argv


def _lint_entry(raw: object, position: int) -> LintCommand:
    """Validate one [[lint]] table from the config file."""
    if not isinstance(raw, dict) or not raw.get("command"):
        raise ValueError(
            f"{CONFIG_FILENAME}: [[lint]] entry {position} must set "
            "'command' (e.g. command = \"npx oxlint\")."
        )
    unknown = sorted(set(raw) - LINT_ENTRY_KEYS)
    if unknown:
        raise ValueError(
            f"{CONFIG_FILENAME}: [[lint]] entry {position} has unknown "
            f"key(s) {unknown}. Allowed: {sorted(LINT_ENTRY_KEYS)}."
        )
    return LintCommand(
        command=raw["command"],
        fix_flag=raw.get("fix_flag"),
        per_file=bool(raw.get("per_file", True)),
        gate=bool(raw.get("gate", True)),
    )


def _normalize_lint_commands(user_config: dict) -> list[LintCommand]:
    """Collapse both config forms into one list, in configured order.

    Any number of commands is allowed. The list form is a TOML array of
    tables, each with its own fix flag and its own role:

        [[lint]]
        command = "npx oxlint"
        fix_flag = "--fix"

        [[lint]]
        command = "npx fallow"
        per_file = false          # project-wide only, never per file
        gate = false              # advisory: failure is not a sprint FAIL

    The older single-command form still works and becomes one entry:

        lint_command = "ruff check"
        lint_fix_flag = "--fix"
    """
    entries = []

    legacy = user_config.get("lint_command")
    if legacy:
        entries.append(
            LintCommand(
                command=legacy,
                fix_flag=user_config.get("lint_fix_flag", DEFAULT_FIX_FLAG),
            )
        )

    raw_entries = user_config.get("lint", [])
    if not isinstance(raw_entries, list):
        raise ValueError(
            f"{CONFIG_FILENAME}: 'lint' must be a list of [[lint]] tables."
        )
    entries.extend(
        _lint_entry(raw, position)
        for position, raw in enumerate(raw_entries, start=1)
    )

    if not entries:
        raise ValueError(
            f"{CONFIG_FILENAME} must define at least one lint command -- "
            "either a [[lint]] entry with a 'command' key, or the "
            "single-command form lint_command = \"ruff check\"."
        )

    return entries


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_config(project_root: Path) -> dict:
    config_path = project_root / CONFIG_FILENAME
    if not config_path.exists():
        raise FileNotFoundError(
            f"No {CONFIG_FILENAME} found in {project_root}. "
            "Create one before running the harness -- see the harness "
            "repo's README for the required fields."
        )

    with open(config_path, "rb") as f:
        user_config = tomllib.load(f)

    config = {**DEFAULT_CONFIG, **user_config}
    config["models"] = {**DEFAULT_CONFIG["models"], **user_config.get("models", {})}
    config["lint"] = _normalize_lint_commands(user_config)

    return config


# ---------------------------------------------------------------------------
# Per-sprint state shared by every role
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Sprint:
    """What every role needs and none of them change."""

    project_root: Path
    config: dict
    explorer: AgentDefinition
    lint_hook: object

    def model(self, role: str) -> str | None:
        return self.config["models"][role]

    def lint_commands(self) -> list[LintCommand]:
        return self.config["lint"]


# ---------------------------------------------------------------------------
# Shared subagent: explorer
# ---------------------------------------------------------------------------

def make_explorer_agent(config: dict) -> AgentDefinition:
    return AgentDefinition(
        description=(
            "Read-only codebase/log/test-output exploration. Use for any "
            "research whose raw output doesn't need to be kept in full."
        ),
        prompt=(
            "You are a read-only research agent. Investigate the question "
            "you're given, then return only a concise summary with "
            "file:line references -- never dump raw file contents or full "
            "command output unless specifically asked to."
        ),
        tools=["Read", "Grep", "Glob", "Bash"],
        model=config["models"]["explorer"],
    )


# ---------------------------------------------------------------------------
# Hook: scoped, auto-fixing lint on every file the generator touches.
# ---------------------------------------------------------------------------

async def _run_lint_on_file(
    project_root: Path, commands: list[LintCommand], file_path: str
) -> list[str]:
    """Run every per-file command, returning one report per failure."""
    problems = []
    for entry in commands:
        process = await asyncio.create_subprocess_exec(
            *entry.argv_for_file(file_path),
            cwd=str(project_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            streams = (
                stdout.decode(errors="replace"),
                stderr.decode(errors="replace"),
            )
            report = "\n".join(part for part in streams if part.strip())
            problems.append(f"$ {entry.command}\n{report}".rstrip())
    return problems


def make_lint_hook(project_root: Path, commands: list[LintCommand]):
    per_file = [entry for entry in commands if entry.per_file]

    async def lint_edited_file(input_data, tool_use_id, context):
        if input_data.get("tool_name") not in {"Write", "Edit"}:
            return {}

        file_path = input_data.get("tool_input", {}).get("file_path")
        if not file_path:
            return {}

        problems = await _run_lint_on_file(project_root, per_file, file_path)
        if not problems:
            return {}  # clean or auto-fixed -- nothing fed back into context

        return {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": (
                    f"Lint issues in {file_path} that could not be "
                    "auto-fixed:\n" + "\n\n".join(problems)
                ),
            }
        }

    return lint_edited_file


# ---------------------------------------------------------------------------
# Role: planner
# ---------------------------------------------------------------------------

async def run_planner(sprint: Sprint, feature_name: str, request: str) -> Path:
    active_dir = sprint.project_root / sprint.config["docs_dir"]
    active_dir.mkdir(parents=True, exist_ok=True)
    plan_file = active_dir / f"{feature_name}.md"

    options = ClaudeAgentOptions(
        system_prompt=(
            "You are a planning agent. Consult the explorer subagent for "
            "any codebase context you need -- don't explore directly. "
            "Produce a numbered task list with acceptance criteria per "
            "task, plus a proposed '## Sprint Contract' section with "
            f"concrete, testable pass/fail criteria. Write the result to "
            f"{plan_file}. Do not write application code."
        ),
        allowed_tools=["Read", "Grep", "Glob", "Write", "Agent"],
        agents={"explorer": sprint.explorer},
        model=sprint.model("planner"),
        cwd=str(sprint.project_root),
    )

    async for message in query(prompt=request, options=options):
        if isinstance(message, ResultMessage) and message.subtype != "success":
            raise RuntimeError(f"Planner failed: {message.subtype}")

    return plan_file


# ---------------------------------------------------------------------------
# Role: generator (multi-turn, resumable across fix-rounds)
# ---------------------------------------------------------------------------

class Generator:
    def __init__(self, sprint: Sprint, plan_file: Path):
        options = ClaudeAgentOptions(
            system_prompt=(
                f"You implement tasks from {plan_file} one at a time. "
                "Work against the agreed Sprint Contract criteria exactly "
                "-- do not expand scope. When you believe a task is "
                "complete, say so explicitly and stop; do not grade your "
                "own work."
            ),
            allowed_tools=["Read", "Edit", "Write", "Bash", "Grep", "Glob", "Agent"],
            agents={"explorer": sprint.explorer},
            hooks={
                "PostToolUse": [
                    HookMatcher(matcher="Write|Edit", hooks=[sprint.lint_hook])
                ]
            },
            model=sprint.model("generator"),
            cwd=str(sprint.project_root),
        )
        self._client = ClaudeSDKClient(options=options)

    async def __aenter__(self):
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc):
        await self._client.__aexit__(*exc)

    async def implement(self, instruction: str) -> str:
        await self._client.query(instruction)
        text = []
        async for message in self._client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text.append(block.text)
        return "\n".join(text)


# ---------------------------------------------------------------------------
# Role: evaluator
# ---------------------------------------------------------------------------

def _lint_instructions(commands: list[LintCommand]) -> str:
    """Tell the evaluator which lint commands bind it and which only inform."""
    gates = [entry.command for entry in commands if entry.gate]
    advisory = [entry.command for entry in commands if not entry.gate]

    parts = []
    if gates:
        listed = ", ".join(f"`{command}`" for command in gates)
        parts.append(
            "Run each of these project-wide and treat any failure as a "
            f"FAIL criterion: {listed}."
        )
    if advisory:
        listed = ", ".join(f"`{command}`" for command in advisory)
        parts.append(
            f"Also run {listed} and summarise the findings in your review, "
            "but do not fail the sprint on them."
        )
    return " ".join(parts)


async def run_evaluator(sprint: Sprint, plan_file: Path) -> tuple[str, str]:
    review_file = plan_file.with_name(plan_file.stem + "-review.md")

    options = ClaudeAgentOptions(
        system_prompt=(
            "You are a skeptical QA reviewer. You did not write this code "
            f"-- grade it critically. Read the Sprint Contract in "
            f"{plan_file}. Check each criterion against the actual code "
            "and mark PASS or FAIL with concrete evidence (file:line or "
            "command output). "
            + _lint_instructions(sprint.lint_commands())
            + f" Write your verdict to {review_file} starting with a line "
            "'STATUS: PASS' or 'STATUS: FAIL', followed by one line per "
            "criterion. Default to FAIL when uncertain."
        ),
        allowed_tools=["Read", "Grep", "Glob", "Bash", "Write"],
        model=sprint.model("evaluator"),
        cwd=str(sprint.project_root),
    )

    async for message in query(prompt=f"Review {plan_file}", options=options):
        if isinstance(message, ResultMessage) and message.subtype != "success":
            raise RuntimeError(f"Evaluator failed: {message.subtype}")

    verdict_text = review_file.read_text()
    status_match = re.search(r"^STATUS:\s*(PASS|FAIL)", verdict_text, re.MULTILINE)
    status = status_match.group(1) if status_match else "FAIL"
    return status, verdict_text


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
    """Loop generator -> evaluator. True if the sprint passed."""
    max_rounds = sprint.config["max_rounds"]

    async with Generator(sprint, plan_file) as generator:
        instruction = f"Implement the tasks in {plan_file}."
        for round_num in range(1, max_rounds + 1):
            print(f"[generator] round {round_num}: implementing...")
            await generator.implement(instruction)

            print(f"[evaluator] round {round_num}: reviewing...")
            status, verdict = await run_evaluator(sprint, plan_file)
            print(f"[evaluator] round {round_num}: {status}")

            if status == "PASS":
                return True

            instruction = (
                "The evaluator found issues. Fix them, then stop. "
                f"Evaluator feedback:\n{verdict}"
            )

    return False


async def run_sprint(project_root: Path, feature_name: str, request: str):
    config = load_config(project_root)
    commands = config["lint"]
    _describe_lint_plan(commands)

    sprint = Sprint(
        project_root=project_root,
        config=config,
        explorer=make_explorer_agent(config),
        lint_hook=make_lint_hook(project_root, commands),
    )

    print(f"[planner] planning '{feature_name}'...")
    plan_file = await run_planner(sprint, feature_name, request)
    print(f"[planner] wrote {plan_file}")

    if await _run_rounds(sprint, plan_file):
        print(f"[orchestrator] sprint '{feature_name}' complete.")
        return

    raise RuntimeError(
        f"Sprint '{feature_name}' did not pass after {config['max_rounds']} "
        "rounds -- stopping instead of looping forever. Inspect the review "
        "file."
    )


# ---------------------------------------------------------------------------
# CLI entry point -- registered as the `harness` console script
# ---------------------------------------------------------------------------

def cli_main():
    parser = argparse.ArgumentParser(prog="harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run", help="Run a sprint for a feature request."
    )
    run_parser.add_argument("request", help="Feature request text.")
    run_parser.add_argument(
        "--project-root", default=".",
        help="Path to the project repo (default: current directory).",
    )

    args = parser.parse_args()

    if args.command == "run":
        project_root = Path(args.project_root).resolve()
        slug = re.sub(r"[^a-z0-9]+", "-", args.request.lower()).strip("-")[:50]
        asyncio.run(run_sprint(project_root, slug, args.request))


if __name__ == "__main__":
    cli_main()
