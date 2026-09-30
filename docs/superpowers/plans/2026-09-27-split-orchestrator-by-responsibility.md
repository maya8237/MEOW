# Split orchestrator.py by Responsibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the single 529-line `src/meow/orchestrator.py` into five files, one per responsibility, per the evaluator agent's own SRP finding, without changing behavior.

**Architecture:** Pure move-and-rewire refactor. `config.py` gets TOML loading and the `LintCommand` model; `sprint.py` gets the shared `Sprint` dataclass; `lint.py` gets the auto-fixing per-file hook; `roles.py` gets the four agent-wiring functions/classes (explorer, planner, `Generator`, evaluator); `orchestrator.py` shrinks to just the round loop (`_run_rounds`, `run_sprint`, `_describe_lint_plan`); `cli.py` gets the argparse entry point. No function's body changes — only imports and module placement.

**Tech Stack:** Python 3.10+, `claude-agent-sdk`, `tomllib`/`tomli`, `ruff` (existing gate). Shell examples below use PowerShell because this repo is checked out on Windows; prefer `./.venv/Scripts/...` and `$env:PATH` syntax instead of bash-only commands.

**Spec:** This plan's spec is the evaluator agent's own verdict from testing `_architecture_review_instructions()` against this repo (this conversation, not a separate file): FAIL on SRP, naming the five concerns — config parsing, agent wiring, lint hooks, orchestration, CLI handling — and recommending exactly this five-way split (`config.py`, `lint.py`, an agents/roles module, a slimmed `orchestrator.py`, `cli.py`).

## Global Constraints

- No behavior change: every function's body is moved verbatim; only imports and module-level placement change.
- No backwards-compatibility shims — nothing outside this package imports `meow.orchestrator` today (confirmed: `src/meow/__init__.py` is empty, `pyproject.toml`'s only entry point is `[project.scripts] harness`), so old import paths are not preserved.
- `ruff check .` (the project's own gate, `pyproject.toml`: `select = ["E","F","W","I","UP","B","SIM","RUF","C90","PLR"]`, mccabe max-complexity 6) must still pass with zero violations after every task.
- Keep each module's docstring accurate to what actually lives in it — that's the whole point of this split.

## Review Focus

- A leftover reference to `meow.orchestrator:cli_main` in `pyproject.toml`'s `[project.scripts]` after `cli_main` moves to `meow.cli` — `harness --help` would fail to resolve the entry point. Task 6 updates this and Task 7's verification step catches it if missed.
- `AGENTS.md`'s "The engine is a single module, `src/meow/orchestrator.py`" line going stale once it no longer is — Task 6 updates it.
- `README.md`'s Layout tree still showing one `orchestrator.py` file — Task 6 updates it.
- A circular import between `roles.py` and `orchestrator.py` if `Sprint` is placed in either of them instead of its own `sprint.py` — this plan avoids it by giving `Sprint` its own module (Task 2) that both `roles.py` and `orchestrator.py` import from, never each other.
- Windows CRLF/line-ending churn on files `git` already tracks with LF — new files should be written with the same LF convention as the file they're extracted from (the existing `.gitattributes`-less repo currently normalizes to LF on `git add`; a stray CRLF-heavy new file would show as a wall of changed lines in review). No action beyond writing plain `\n` newlines, matching how these files already look on disk before Git's own autocrlf touches them.

---

### Task 1: Extract `config.py`

**Files:**
- Create: `src/meow/config.py`
- Modify: `src/meow/orchestrator.py` (remove the moved code; add `from meow.config import (...)`)

**Interfaces:**
- Produces: `CONFIG_FILENAME: str`, `DEFAULT_CONFIG: dict`, `LintCommand` (frozen dataclass with `command: str`, `fix_flag: str | None = None`, `per_file: bool = True`, `gate: bool = True`, methods `argv() -> list[str]` and `argv_for_file(file_path: str) -> list[str]`), `load_config(project_root: Path) -> dict`.

- [ ] **Step 1: Create `src/meow/config.py`** with this exact content:

```python
"""
meow/config.py

Lint-command modeling and .harness.toml loading -- the project-specific
values every role reads through a `Sprint`, kept separate from the agent
wiring and orchestration that consume them.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # Python 3.10 fallback -- pip install tomli

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
```

- [ ] **Step 2: Remove the moved code from `src/meow/orchestrator.py`**

Delete lines 42-192 of the current file (the `CONFIG_FILENAME`/`DEFAULT_FIX_FLAG`/`LINT_ENTRY_KEYS`/`DEFAULT_CONFIG` constants, the `# Lint commands` section comment block, `LintCommand`, `_lint_entry`, `_normalize_lint_commands`, the `# Config loading` section comment block, and `load_config`). Leave the `# Per-sprint state shared by every role` section (currently starting at line 195) and everything below it in place for now — later tasks move those.

- [ ] **Step 3: Verify no other code in `orchestrator.py` breaks**

Run: `cd C:\Users\m\Documents\MEOW && .venv/Scripts/ruff.exe check src/meow/orchestrator.py src/meow/config.py`
Expected: reports `F821` (undefined name) for every symbol `orchestrator.py` still uses from the deleted block (`LintCommand`, `load_config`, `DEFAULT_CONFIG`, etc.) — this is expected at this point, not a failure of this task. Confirm `config.py` alone reports zero violations: `.venv/Scripts/ruff.exe check src/meow/config.py` must print `All checks passed!`.

- [ ] **Step 4: Commit**

```bash
git add src/meow/config.py src/meow/orchestrator.py
git commit -m "refactor: extract lint-command model and config loading into meow.config"
```

---

### Task 2: Extract `sprint.py`

**Files:**
- Create: `src/meow/sprint.py`
- Modify: `src/meow/orchestrator.py` (remove `Sprint`; add `from meow.sprint import Sprint`)

**Interfaces:**
- Consumes: `meow.config.LintCommand` (for the `lint_commands()` return type).
- Produces: `Sprint` (frozen dataclass: `project_root: Path`, `config: dict`, `explorer: AgentDefinition`, `lint_hook: object`; methods `model(role: str) -> str | None` and `lint_commands() -> list[LintCommand]`).

- [ ] **Step 1: Create `src/meow/sprint.py`** with this exact content:

```python
"""
meow/sprint.py

Sprint: the per-sprint state every role reads and none of them mutate. Kept
in its own module so the agent-wiring roles and the orchestration loop can
both import it without either depending on the other.
"""

from dataclasses import dataclass
from pathlib import Path

from claude_agent_sdk import AgentDefinition

from meow.config import LintCommand


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
```

- [ ] **Step 2: Remove the `Sprint` class and its section header from `src/meow/orchestrator.py`**

Delete the `# Per-sprint state shared by every role` comment block and the `Sprint` dataclass definition.

- [ ] **Step 3: Verify**

Run: `cd C:\Users\m\Documents\MEOW && .venv/Scripts/ruff.exe check src/meow/sprint.py`
Expected: `All checks passed!`. (`orchestrator.py` will still show `F821` for `Sprint` and the config-related names until Task 5 — expected.)

- [ ] **Step 4: Commit**

```bash
git add src/meow/sprint.py src/meow/orchestrator.py
git commit -m "refactor: extract Sprint into meow.sprint"
```

---

### Task 3: Extract `lint.py`

**Files:**
- Create: `src/meow/lint.py`
- Modify: `src/meow/orchestrator.py` (remove `_run_lint_on_file`/`make_lint_hook`; these will be re-imported once `orchestrator.py` needs `make_lint_hook` again in Task 5, but for now just delete)

**Interfaces:**
- Consumes: `meow.config.LintCommand`.
- Produces: `make_lint_hook(project_root: Path, commands: list[LintCommand])` returning an async `PostToolUse` hook callable.

- [ ] **Step 1: Create `src/meow/lint.py`** with this exact content:

```python
"""
meow/lint.py

The generator's auto-fixing, per-file lint hook: runs every configured
per-file command on each file the generator writes, feeding unfixable
failures back into the generator's context as additional tool-use output.
"""

import asyncio
from pathlib import Path

from meow.config import LintCommand


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
```

- [ ] **Step 2: Remove `_run_lint_on_file` and `make_lint_hook` (and their section header) from `src/meow/orchestrator.py`**

Delete the `# Hook: scoped, auto-fixing lint on every file the generator touches.` comment block and both functions.

- [ ] **Step 3: Verify**

Run: `cd C:\Users\m\Documents\MEOW && .venv/Scripts/ruff.exe check src/meow/lint.py`
Expected: `All checks passed!`.

- [ ] **Step 4: Commit**

```bash
git add src/meow/lint.py src/meow/orchestrator.py
git commit -m "refactor: extract the per-file lint hook into meow.lint"
```

---

### Task 4: Extract `roles.py`

**Files:**
- Create: `src/meow/roles.py`
- Modify: `src/meow/orchestrator.py` (remove `make_explorer_agent`, `run_planner`, `Generator`, `_lint_instructions`, `_architecture_review_instructions`, `run_evaluator`)

**Interfaces:**
- Consumes: `meow.sprint.Sprint`, `meow.config.LintCommand`.
- Produces: `make_explorer_agent(config: dict) -> AgentDefinition`, `run_planner(sprint: Sprint, feature_name: str, request: str) -> Path`, `Generator` (async context manager with `async def implement(self, instruction: str) -> str`), `run_evaluator(sprint: Sprint, plan_file: Path) -> tuple[str, str]`.

- [ ] **Step 1: Create `src/meow/roles.py`** with this exact content:

```python
"""
meow/roles.py

The four peer agents -- explorer, planner, generator, evaluator -- as real
Claude Agent SDK sessions. Each role takes a `Sprint` for the
project-specific values it needs (models, lint commands, project root) and
nothing else; none of them know where those values came from.
"""

import re
from pathlib import Path

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

from meow.config import LintCommand
from meow.sprint import Sprint


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


def _architecture_review_instructions() -> str:
    """Tell the evaluator to look for monolithic, SRP-breaking modules."""
    return (
        "Also perform a SOLID/SRP review. Flag any file or class that mixes "
        "multiple responsibilities, such as config parsing + agent wiring + "
        "lint hooks + orchestration + CLI handling in one module. Treat any "
        "single file that does more than one broad concern as a FAIL criterion "
        "unless the code is clearly split into cohesive helpers or classes. "
        "Use file:line evidence; do not accept 'it works' as an excuse for "
        "a monolithic design."
    )


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
            + " "
            + _architecture_review_instructions()
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
```

- [ ] **Step 2: Remove the moved functions/class from `src/meow/orchestrator.py`**

Delete the `# Shared subagent: explorer`, `# Role: planner`, `# Role: generator (multi-turn, resumable across fix-rounds)`, and `# Role: evaluator` section blocks in their entirety (`make_explorer_agent`, `run_planner`, `Generator`, `_lint_instructions`, `_architecture_review_instructions`, `run_evaluator`).

- [ ] **Step 3: Verify**

Run: `cd C:\Users\m\Documents\MEOW && .venv/Scripts/ruff.exe check src/meow/roles.py`
Expected: `All checks passed!`.

- [ ] **Step 4: Commit**

```bash
git add src/meow/roles.py src/meow/orchestrator.py
git commit -m "refactor: extract the four agent roles into meow.roles"
```

---

### Task 5: Slim `orchestrator.py` down to the round loop

**Files:**
- Modify: `src/meow/orchestrator.py` (rewrite in full — after Tasks 1-4 it should contain only the orchestration section, the module docstring, and imports)

**Interfaces:**
- Consumes: `meow.config.{load_config, LintCommand}`, `meow.lint.make_lint_hook`, `meow.roles.{make_explorer_agent, run_planner, Generator, run_evaluator}`, `meow.sprint.Sprint`.
- Produces: `_describe_lint_plan(commands: list[LintCommand]) -> None`, `run_sprint(project_root: Path, feature_name: str, request: str)` (async), `_run_rounds` (async, unchanged signature).

- [ ] **Step 1: Rewrite `src/meow/orchestrator.py` in full** with this exact content:

```python
"""
meow/orchestrator.py

The sprint-level control flow: plan once, then loop generator -> evaluator
rounds until the evaluator passes the sprint or max_rounds runs out. All
four agents are wired in `meow.roles`; project config comes from
`meow.config`. This module owns only the round-by-round policy.
"""

from pathlib import Path

from meow.config import LintCommand, load_config
from meow.lint import make_lint_hook
from meow.roles import Generator, make_explorer_agent, run_evaluator, run_planner
from meow.sprint import Sprint


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
```

- [ ] **Step 2: Verify**

Run: `cd C:\Users\m\Documents\MEOW && .venv/Scripts/ruff.exe check .`
Expected: `All checks passed!` across the whole project — this is the first point where every module (`config.py`, `sprint.py`, `lint.py`, `roles.py`, `orchestrator.py`) exists and cross-references resolve, so `F821` undefined-name errors from Tasks 1-4 should now be gone. `cli_main` doesn't exist yet (moves in Task 6), so nothing currently imports `orchestrator.cli_main` — the package isn't importable as a CLI yet, which is expected until Task 6.

- [ ] **Step 3: Commit**

```bash
git add src/meow/orchestrator.py
git commit -m "refactor: slim orchestrator.py down to the round loop"
```

---

### Task 6: Extract `cli.py` and fix up `pyproject.toml`, `AGENTS.md`, `README.md`

**Files:**
- Create: `src/meow/cli.py`
- Modify: `src/meow/orchestrator.py` (remove `cli_main` and the `if __name__ == "__main__"` block and the CLI section header)
- Modify: `pyproject.toml:13` (`[project.scripts]` entry)
- Modify: `AGENTS.md` (the `## Layout` section)
- Modify: `README.md` (the `## Layout` section's file tree)

**Interfaces:**
- Consumes: `meow.orchestrator.run_sprint`.
- Produces: `cli_main()` — the `harness` console-script entry point.

- [ ] **Step 1: Create `src/meow/cli.py`** with this exact content:

```python
"""
meow/cli.py

The `harness` console-script entry point registered in pyproject.toml:
argument parsing and dispatch into meow.orchestrator.run_sprint.
"""

import argparse
import asyncio
import re
from pathlib import Path

from meow.orchestrator import run_sprint


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
```

- [ ] **Step 2: Remove `cli_main`, the `if __name__ == "__main__"` block, and the `# CLI entry point` section header from `src/meow/orchestrator.py`**

After this, `orchestrator.py` ends at `run_sprint`'s closing `raise RuntimeError(...)` block — no code below it.

- [ ] **Step 3: Update `pyproject.toml`**

Change:
```toml
[project.scripts]
harness = "meow.orchestrator:cli_main"
```
to:
```toml
[project.scripts]
harness = "meow.cli:cli_main"
```

- [ ] **Step 4: Update `AGENTS.md`'s `## Layout` section**

Replace:
```markdown
## Layout

The engine is a single module, `src/meow/orchestrator.py`. The `src/` layout is
deliberate: code run from the repo root reaches the *installed* copy, so a
broken editable install is caught rather than masked.
```
with:
```markdown
## Layout

The engine is split by responsibility under `src/meow/`: `config.py`
(`.harness.toml` loading and the lint-command model), `sprint.py` (the shared
per-sprint state), `lint.py` (the auto-fixing per-file hook), `roles.py` (the
explorer/planner/generator/evaluator agents), `orchestrator.py` (the
generator <-> evaluator round loop), and `cli.py` (the `harness`
console-script entry point). The `src/` layout is deliberate: code run from
the repo root reaches the *installed* copy, so a broken editable install is
caught rather than masked.
```

- [ ] **Step 5: Update `README.md`'s `## Layout` section**

Replace:
````markdown
## Layout

```
meow/
├── pyproject.toml
├── README.md
├── docs/
│   └── harness-split-repo-handoff.md   # why the engine lives in its own repo
├── templates/
│   └── harness.toml.example            # copy into consuming projects
└── src/
    └── meow/
        ├── __init__.py
        └── orchestrator.py             # the engine
```
````
with:
````markdown
## Layout

```
meow/
├── pyproject.toml
├── README.md
├── docs/
│   └── harness-split-repo-handoff.md   # why the engine lives in its own repo
├── templates/
│   └── harness.toml.example            # copy into consuming projects
└── src/
    └── meow/
        ├── __init__.py
        ├── config.py                   # .harness.toml loading + lint-command model
        ├── sprint.py                    # per-sprint state shared by every role
        ├── lint.py                      # auto-fixing per-file lint hook
        ├── roles.py                     # explorer/planner/generator/evaluator agents
        ├── orchestrator.py              # generator <-> evaluator round loop
        └── cli.py                       # `harness` console-script entry point
```
````

- [ ] **Step 6: Verify the package is importable and the console script resolves**

Run:
```bash
cd C:\Users\m\Documents\MEOW
.venv/Scripts/python.exe -c "import meow.cli, meow.orchestrator, meow.roles, meow.lint, meow.sprint, meow.config; print('ok')"
.venv/Scripts/ruff.exe check .
.venv/Scripts/harness --help
```
Expected: `ok`, then `All checks passed!`, then argparse's usage text for `harness` printing without a traceback (confirms the `[project.scripts]` entry point in the editable install resolves to `meow.cli:cli_main`).

- [ ] **Step 7: Commit**

```bash
git add src/meow/cli.py src/meow/orchestrator.py pyproject.toml AGENTS.md README.md
git commit -m "refactor: extract cli.py and update docs/entry-point for the module split"
```

---

### Task 7: Close the loop — re-run the evaluator against the split

**Files:**
- None created or modified — this task only runs the existing evaluator smoke-test harness from earlier in this session against the now-split code, to confirm the SRP finding that motivated this whole plan is resolved.

**Interfaces:**
- Consumes: `meow.config.load_config`, `meow.sprint.Sprint`, `meow.roles.make_explorer_agent`, `meow.lint.make_lint_hook`, `meow.roles.run_evaluator` (note the import paths changed from the pre-split `meow.orchestrator` — the standalone test script from earlier in this session must be updated to match, or written fresh).

- [ ] **Step 1: Write (or update) the standalone evaluator smoke-test script**

```python
"""Standalone smoke test: run the real evaluator agent against meow's own
(now-split) source tree, to confirm the SOLID/SRP review no longer fails on
a monolithic orchestrator.py."""

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(r"C:\Users\m\Documents\MEOW").resolve()
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from meow.config import load_config  # noqa: E402
from meow.lint import make_lint_hook  # noqa: E402
from meow.roles import make_explorer_agent, run_evaluator  # noqa: E402
from meow.sprint import Sprint  # noqa: E402

PLAN_FILE = PROJECT_ROOT / "docs" / "exec-plans" / "active" / "evaluator-smoke-test.md"
REVIEW_FILE = PLAN_FILE.with_name(PLAN_FILE.stem + "-review.md")


async def main():
    config = load_config(PROJECT_ROOT)
    commands = config["lint"]

    sprint = Sprint(
        project_root=PROJECT_ROOT,
        config=config,
        explorer=make_explorer_agent(config),
        lint_hook=make_lint_hook(PROJECT_ROOT, commands),
    )

    PLAN_FILE.parent.mkdir(parents=True, exist_ok=True)
    PLAN_FILE.write_text(
        "# Sprint: evaluator-smoke-test\n\n"
        "## Sprint Contract\n"
        "- [ ] The harness engine under `src/meow/` implements the "
        "planner/generator/evaluator loop and the project lints clean "
        "(`ruff check`).\n",
        encoding="utf-8",
    )

    try:
        status, verdict = await run_evaluator(sprint, PLAN_FILE)
        print("=== STATUS ===")
        print(status)
        print("=== VERDICT ===")
        print(verdict)
    finally:
        PLAN_FILE.unlink(missing_ok=True)
        REVIEW_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
```

Save it to the scratchpad, not the repo (it is a one-off verification, like the one used earlier in this session).

- [ ] **Step 2: Run it**

```powershell
cd C:\Users\m\Documents\MEOW
$env:PATH = "C:\Users\m\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude-code\2.1.247;$env:PATH"
.\.venv\Scripts\python.exe <path-to-script>
```
(The `PATH` update makes the standalone `claude.exe` the SDK shells out to discoverable — same workaround needed earlier this session. If a newer Claude Code build has since changed that folder name, re-resolve it via `$env:CLAUDE_CODE_EXECPATH` or `Get-ChildItem` in a real terminal first.)

Expected: `STATUS: PASS` on the SOLID/SRP criterion specifically — the verdict text should no longer name `orchestrator.py` as mixing five concerns, since each concern now has its own file. (The lint-gate criterion was already passing before this plan and should remain so.) If the SRP criterion still fails, read the verdict's evidence — it may be pointing at a genuinely remaining seam (e.g., `Sprint` still needing to know about both `config` and `roles`) rather than a false positive; do not treat "the evaluator is wrong" as the default explanation without checking the file:line evidence first.

- [ ] **Step 3: Report the verdict**

No commit for this task — it's verification only. Report the STATUS and the SRP section of the verdict back to the user.
