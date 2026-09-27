"""
meow/orchestrator.py

The generic harness engine: explorer, planner, generator, and evaluator as
real peer agents, coordinated by plain Python control flow. Unlike the
single-project version, all project-specific values (lint command, models,
round cap) are read from a `.harness.toml` file in the target project's
root, not hardcoded here -- this file is meant to be installed once and
reused across projects.

Install (from the harness repo root):    pip install -e .
Run (from inside a project repo):        harness run "Add CSV export"
"""

import argparse
import asyncio
import re
import subprocess
import sys
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

DEFAULT_CONFIG = {
    "lint_command": None,           # required -- e.g. "ruff check"
    "lint_fix_flag": "--fix",
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

    if not config["lint_command"]:
        raise ValueError(
            f"{CONFIG_FILENAME} must set 'lint_command' (e.g. "
            '\'lint_command = "ruff check"\').'
        )

    return config


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

def make_lint_hook(config: dict):
    lint_cmd = config["lint_command"].split() + [config["lint_fix_flag"]]

    async def lint_edited_file(input_data, tool_use_id, context):
        if input_data.get("tool_name") not in ("Write", "Edit"):
            return {}

        file_path = input_data.get("tool_input", {}).get("file_path")
        if not file_path:
            return {}

        result = subprocess.run(
            [*lint_cmd, file_path], capture_output=True, text=True
        )

        if result.returncode == 0:
            return {}  # clean or auto-fixed -- nothing fed back into context

        return {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": (
                    f"Lint issues in {file_path} that could not be "
                    f"auto-fixed:\n{result.stdout}\n{result.stderr}"
                ),
            }
        }

    return lint_edited_file


# ---------------------------------------------------------------------------
# Role: planner
# ---------------------------------------------------------------------------

async def run_planner(
    project_root: Path, config: dict, explorer_agent: AgentDefinition,
    feature_name: str, request: str,
) -> Path:
    active_dir = project_root / config["docs_dir"]
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
        agents={"explorer": explorer_agent},
        model=config["models"]["planner"],
        cwd=str(project_root),
    )

    async for message in query(prompt=request, options=options):
        if isinstance(message, ResultMessage) and message.subtype != "success":
            raise RuntimeError(f"Planner failed: {message.subtype}")

    return plan_file


# ---------------------------------------------------------------------------
# Role: generator (multi-turn, resumable across fix-rounds)
# ---------------------------------------------------------------------------

class Generator:
    def __init__(
        self, project_root: Path, config: dict, explorer_agent: AgentDefinition,
        lint_hook, plan_file: Path,
    ):
        options = ClaudeAgentOptions(
            system_prompt=(
                f"You implement tasks from {plan_file} one at a time. "
                "Work against the agreed Sprint Contract criteria exactly "
                "-- do not expand scope. When you believe a task is "
                "complete, say so explicitly and stop; do not grade your "
                "own work."
            ),
            allowed_tools=["Read", "Edit", "Write", "Bash", "Grep", "Glob", "Agent"],
            agents={"explorer": explorer_agent},
            hooks={"PostToolUse": [HookMatcher(matcher="Write|Edit", hooks=[lint_hook])]},
            model=config["models"]["generator"],
            cwd=str(project_root),
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

async def run_evaluator(project_root: Path, config: dict, plan_file: Path) -> tuple[str, str]:
    review_file = plan_file.with_name(plan_file.stem + "-review.md")
    check_cmd = config["lint_command"]  # check-only, no fix flag

    options = ClaudeAgentOptions(
        system_prompt=(
            "You are a skeptical QA reviewer. You did not write this code "
            f"-- grade it critically. Read the Sprint Contract in "
            f"{plan_file}. Check each criterion against the actual code "
            "and mark PASS or FAIL with concrete evidence (file:line or "
            f"command output). Also run `{check_cmd}` project-wide and "
            "treat any failure as a FAIL criterion. Write your verdict to "
            f"{review_file} starting with a line 'STATUS: PASS' or "
            "'STATUS: FAIL', followed by one line per criterion. Default "
            "to FAIL when uncertain."
        ),
        allowed_tools=["Read", "Grep", "Glob", "Bash", "Write"],
        model=config["models"]["evaluator"],
        cwd=str(project_root),
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

async def run_sprint(project_root: Path, feature_name: str, request: str):
    config = load_config(project_root)
    explorer_agent = make_explorer_agent(config)
    lint_hook = make_lint_hook(config)
    max_rounds = config["max_rounds"]

    print(f"[planner] planning '{feature_name}'...")
    plan_file = await run_planner(project_root, config, explorer_agent, feature_name, request)
    print(f"[planner] wrote {plan_file}")

    async with Generator(project_root, config, explorer_agent, lint_hook, plan_file) as generator:
        instruction = f"Implement the tasks in {plan_file}."
        for round_num in range(1, max_rounds + 1):
            print(f"[generator] round {round_num}: implementing...")
            await generator.implement(instruction)

            print(f"[evaluator] round {round_num}: reviewing...")
            status, verdict = await run_evaluator(project_root, config, plan_file)
            print(f"[evaluator] round {round_num}: {status}")

            if status == "PASS":
                print(f"[orchestrator] sprint '{feature_name}' complete.")
                return

            instruction = (
                "The evaluator found issues. Fix them, then stop. "
                f"Evaluator feedback:\n{verdict}"
            )

    raise RuntimeError(
        f"Sprint '{feature_name}' did not pass after {max_rounds} rounds -- "
        "stopping instead of looping forever. Inspect the review file."
    )


# ---------------------------------------------------------------------------
# CLI entry point -- registered as the `harness` console script
# ---------------------------------------------------------------------------

def cli_main():
    parser = argparse.ArgumentParser(prog="harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run a sprint for a feature request.")
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
