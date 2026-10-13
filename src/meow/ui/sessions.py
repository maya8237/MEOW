"""Validated launch recipes for the local UI sessions API."""

from __future__ import annotations

import argparse
import contextlib
import io
import re
import shlex
import shutil
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, TypedDict

from meow.cli.parser import _build_arg_parser
from meow.cli.review_cli import _validate_review_flags
from meow.cli.validation import (
    _validate_ci_flags,
    _validate_feature_name_requirement,
    _validate_run_flags,
)


class RecipeError(ValueError):
    """A recipe cannot be safely translated to a supported launch."""


class Capabilities(TypedDict):
    available: bool
    version: str | None
    missing: list[str]
    permission_policy: Literal["dontAsk+none", "unsupported"]
    bypass_supported: bool
    error: str | None


@dataclass(frozen=True)
class Recipe:
    id: str
    name: str
    repo: str
    mode: Literal["cli", "claude"]
    command: str
    request: str
    workflow: str | None
    inputs: dict[str, str]
    arguments: list[str]
    options: dict[str, bool | str]


_CLI_COMMANDS = {"run", "plan", "review"}
_PROHIBITED_ARGUMENTS = {
    "--work-dir",
    "-d",
    "--background",
    "--manually-approve-plan",
    "-m",
    "--no-browser",
}
_SHORT_GROUP_MIN_LENGTH = 3
_REQUIRED_CLAUDE_CAPABILITIES = (
    ("--print", r"(?<![\w-])--print(?![\w-])", "--print"),
    (
        "verbose stream-json",
        r"(?=.*(?<![\w-])--verbose(?![\w-]))(?=.*(?<![\w-])--output-format(?![\w-]))(?=.*stream-json)",
        "verbose stream-json output",
    ),
    (
        "partial messages",
        r"(?<![\w-])--include-partial-messages(?![\w-])",
        "partial messages",
    ),
    ("--resume", r"(?<![\w-])--resume(?![\w-])", "--resume"),
    (
        "--permission-mode dontAsk",
        r"(?=.*(?<![\w-])--permission-mode(?![\w-]))(?=.*dontAsk)",
        "--permission-mode dontAsk",
    ),
    (
        "--permission-prompts none",
        r"(?=.*(?<![\w-])--permission-prompts(?![\w-]))(?=.*\bnone\b)",
        "--permission-prompts none",
    ),
)


def new_id() -> str:
    """Return a server-generated UUID-derived identifier."""
    return uuid.uuid4().hex


def recipe_json(recipe: Recipe) -> dict:
    return asdict(recipe)


def _text(payload: dict, key: str, *, required: bool = True) -> str:
    value = payload.get(key)
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise RecipeError(f"{key} must be a non-empty string")
    if "\x00" in value:
        raise RecipeError(f"{key} cannot contain a NUL character")
    return value


def _parser_for(parser: argparse.ArgumentParser, command: str):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices[command]
    raise RecipeError(f"Unsupported MEOW command: {command}")


def _option_actions(parser: argparse.ArgumentParser):
    return {
        option: action for action in parser._actions for option in action.option_strings
    }


def _check_argument_tokens(arguments: list[str]) -> None:
    for argument in arguments:
        option = argument.split("=", 1)[0]
        if option in _PROHIBITED_ARGUMENTS or any(
            option.startswith(prefix + "=") for prefix in _PROHIBITED_ARGUMENTS
        ):
            raise RecipeError(f"{option} is controlled by the UI session server")
        if (
            option.startswith("-")
            and not option.startswith("--")
            and len(option) >= _SHORT_GROUP_MIN_LENGTH
        ):
            # Short option groups can hide duplicate actions or interactive flags.
            short_option = option[:2]
            if short_option in _PROHIBITED_ARGUMENTS:
                raise RecipeError(f"{short_option} is not allowed in a UI recipe")


def _record_option(option: str, command: str, known: dict, seen: set[str]) -> None:
    action = known.get(option)
    if action is None:
        if option.startswith("--"):
            raise RecipeError(f"Unknown {command} option: {option}")
        return
    if action.dest not in seen or action.__class__.__name__ == "_AppendAction":
        seen.add(action.dest)
        return
    raise RecipeError(f"Duplicate singleton option: {option}")


def _check_unique_options(command: str, final_options: list[str]) -> None:
    known = _option_actions(_parser_for(_build_arg_parser(), command))
    seen: set[str] = set()
    for token in final_options:
        if token.startswith("-"):
            _record_option(token.split("=", 1)[0], command, known, seen)


def _parse_arguments(command: str, final_options: list[str], request: str):
    parser = _build_arg_parser()
    argv = [command, *final_options]
    if request.startswith("-"):
        argv.append("--")
    argv.append(request)
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            parsed = parser.parse_args(argv)
    except SystemExit as exc:
        raise RecipeError("Invalid MEOW command arguments") from exc
    return parser, parsed


def _validate_semantics(command: str, repo: Path, parser, parsed) -> None:
    try:
        if command == "review":
            _validate_ci_flags(parser, parsed)
            _validate_review_flags(
                parsed.request,
                parsed.jira,
                parsed.gitlab,
                parsed.branch,
                parsed.target,
                Path(parsed.plan) if parsed.plan else None,
                Path(parsed.review_file) if parsed.review_file else None,
                github_link=parsed.github,
                test=parsed.test,
            )
        if command in {"run", "plan"}:
            _validate_feature_name_requirement(parser, parsed, repo)
        if command == "run":
            _validate_run_flags(parser, parsed)
    except SystemExit as exc:
        raise RecipeError("Invalid MEOW command arguments") from exc
    except ValueError as exc:
        raise RecipeError(str(exc)) from exc


def _validate_arguments(recipe: Recipe, generated: list[str]) -> None:
    _check_argument_tokens(recipe.arguments)
    final_options = [*generated, *recipe.arguments]
    _check_unique_options(recipe.command, final_options)
    parser, parsed = _parse_arguments(recipe.command, final_options, recipe.request)
    _validate_semantics(recipe.command, Path(recipe.repo), parser, parsed)
    if (
        parsed.command != recipe.command
        or getattr(parsed, "request", None) != recipe.request
    ):
        raise RecipeError("Arguments cannot replace the recipe request")
    if (
        recipe.command in {"run", "plan"}
        and getattr(parsed, "feature_name", None) != recipe.name
    ):
        raise RecipeError("Arguments cannot replace the recipe name")


def _selected_repo(repo: Path, payload: dict) -> Path:
    repo_path = repo.resolve()
    supplied_repo = payload.get("repo")
    if supplied_repo is not None and (
        not isinstance(supplied_repo, str) or Path(supplied_repo).resolve() != repo_path
    ):
        raise RecipeError("recipe repository does not match the selected repository")
    return repo_path


def _validated_inputs(payload: dict) -> dict[str, str]:
    inputs = payload.get("inputs", {})
    if not isinstance(inputs, dict) or any(
        not isinstance(key, str)
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
        or not isinstance(value, str)
        or "\x00" in value
        for key, value in inputs.items()
    ):
        raise RecipeError("inputs must map identifier names to string values")
    return dict(inputs)


def _validated_arguments(payload: dict) -> list[str]:
    arguments = payload.get("arguments", [])
    if not isinstance(arguments, list) or any(
        not isinstance(arg, str) or not arg or "\x00" in arg for arg in arguments
    ):
        raise RecipeError("arguments must contain non-empty strings without NULs")
    return list(arguments)


def _validated_claude_options(options: dict) -> dict[str, bool | str]:
    if set(options) - {"permission_policy"}:
        raise RecipeError("Unsupported Claude options: " + ", ".join(sorted(options)))
    policy = options.get("permission_policy", "default")
    if policy not in {"default", "bypass"}:
        raise RecipeError("permission_policy must be 'default' or 'bypass'")
    options["permission_policy"] = policy
    return options


def _validated_cli_options(options: dict, command: str) -> dict[str, bool | str]:
    if set(options) - {"no_worktree", "unattended"}:
        raise RecipeError("Unsupported CLI options: " + ", ".join(sorted(options)))
    if any(not isinstance(item, bool) for item in options.values()):
        raise RecipeError("CLI options must be boolean")
    if command not in {"run", "plan"} and "no_worktree" in options:
        raise RecipeError("no_worktree is available only for run and plan recipes")
    if command != "run" and "unattended" in options:
        raise RecipeError("unattended is available only for run recipes")
    return options


def _validated_options(mode: str, command: str, value: object) -> dict[str, bool | str]:
    if not isinstance(value, dict) or any(
        not isinstance(option, str) or not isinstance(item, (bool, str))
        for option, item in value.items()
    ):
        raise RecipeError("options must map names to boolean or string values")
    options = dict(value)
    if mode == "claude":
        return _validated_claude_options(options)
    return _validated_cli_options(options, command)


def _validate_mode_command(mode: object, command: str) -> None:
    if mode not in {"cli", "claude"}:
        raise RecipeError("mode must be 'cli' or 'claude'")
    if mode == "cli" and command not in _CLI_COMMANDS:
        raise RecipeError(f"Unsupported headless MEOW command: {command}")


def validate_recipe(repo: Path, payload: object) -> Recipe:
    if not isinstance(payload, dict):
        raise RecipeError("recipe must be an object")
    repo_path = _selected_repo(Path(repo), payload)
    name = _text(payload, "name")
    if name.startswith("-"):
        raise RecipeError("name cannot start with a dash")
    mode = payload.get("mode")
    command = _text(payload, "command")
    request = _text(payload, "request")
    _validate_mode_command(mode, command)

    workflow_value = payload.get("workflow")
    if workflow_value is not None and (
        not isinstance(workflow_value, str)
        or not workflow_value.strip()
        or "\x00" in workflow_value
    ):
        raise RecipeError("workflow must be a non-empty string or null")
    workflow = workflow_value

    recipe = Recipe(
        id=new_id(),
        name=name,
        repo=str(repo_path),
        mode=mode,
        command=command,
        request=request,
        workflow=workflow,
        inputs=_validated_inputs(payload),
        arguments=_validated_arguments(payload),
        options=_validated_options(mode, command, payload.get("options", {})),
    )
    if mode == "cli":
        command_for(recipe)
    return recipe


def _generated_cli_options(recipe: Recipe) -> list[str]:
    options = recipe.options
    generated: list[str] = []
    if recipe.command in {"run", "plan"}:
        generated.extend(["--name", recipe.name])
    if recipe.workflow is not None and recipe.command != "run":
        raise RecipeError("workflow is supported only for run recipes")
    if recipe.workflow is not None:
        generated.extend(["--workflow", recipe.workflow])
    if recipe.command == "run":
        generated.extend(_run_cli_options(recipe))
    generated.extend(_worktree_cli_options(options))
    if recipe.inputs and recipe.command != "run":
        raise RecipeError("workflow inputs are supported only for run recipes")
    return generated


def _run_cli_options(recipe: Recipe) -> list[str]:
    options = [
        token
        for key, value in recipe.inputs.items()
        for token in ("--input", f"{key}={value}")
    ]
    no_worktree = bool(recipe.options.get("no_worktree", False))
    if recipe.options.get("unattended", not no_worktree):
        options.append("--unattended")
    return options


def _worktree_cli_options(options: dict[str, bool | str]) -> list[str]:
    return ["--no-worktree"] if options.get("no_worktree") else []


def _cli_argv(recipe: Recipe) -> list[str]:
    generated = _generated_cli_options(recipe)
    _validate_arguments(
        recipe,
        generated,
    )
    return [
        sys.executable,
        "-u",
        "-m",
        "meow",
        recipe.command,
        *generated,
        *recipe.arguments,
        recipe.request,
    ]


def claude_path() -> str | None:
    return shutil.which("claude")


def claude_help() -> str:
    executable = claude_path()
    if not executable:
        raise RecipeError("Claude Code is not installed or is not available on PATH")
    try:
        result = subprocess.run(
            [executable, "--help"],
            capture_output=True,
            text=True,
            timeout=5,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RecipeError(f"Could not inspect Claude Code capabilities: {exc}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        raise RecipeError(
            f"Claude Code capability check failed: {detail or result.returncode}"
        )
    return result.stdout + "\n" + result.stderr


def _capabilities_from_help(help_text: str) -> tuple[list[str], bool]:
    missing = [
        label
        for _, pattern, label in _REQUIRED_CLAUDE_CAPABILITIES
        if not re.search(pattern, help_text, re.IGNORECASE | re.DOTALL)
    ]
    bypass_supported = (
        "--allow-dangerously-skip-permissions" in help_text
        and "bypassPermissions" in help_text
    )
    return missing, bypass_supported


def detect_capabilities(repo: Path) -> Capabilities:
    del repo  # Capability discovery must never inspect or execute project config.
    executable = claude_path()
    if not executable:
        return {
            "available": False,
            "version": None,
            "missing": ["Claude Code executable"],
            "permission_policy": "unsupported",
            "bypass_supported": False,
            "error": "Claude Code is not installed or is not available on PATH",
        }
    help_text = claude_help()
    missing, bypass_supported = _capabilities_from_help(help_text)
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            shell=False,
            check=False,
        )
        version = (result.stdout or result.stderr).strip().splitlines()[0]
        if result.returncode:
            version = None
    except (OSError, subprocess.SubprocessError, IndexError):
        version = None
    if missing:
        raise RecipeError(
            "Claude Code is missing required capabilities: " + ", ".join(missing)
        )
    return {
        "available": True,
        "version": version,
        "missing": [],
        "permission_policy": "dontAsk+none",
        "bypass_supported": bypass_supported,
        "error": None,
    }


def command_for(recipe: Recipe, python: str = sys.executable) -> list[str]:
    if recipe.mode == "cli":
        argv = _cli_argv(recipe)
        argv[0] = python
        return argv
    executable = claude_path()
    if not executable:
        raise RecipeError("Claude Code is not installed or is not available on PATH")
    capabilities = detect_capabilities(Path(recipe.repo))
    policy = recipe.options.get("permission_policy", "default")
    if policy == "bypass" and not capabilities["bypass_supported"]:
        raise RecipeError(
            "Installed Claude Code does not support the paired bypass flags"
        )
    argv = [
        executable,
        "--print",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--permission-mode",
        "dontAsk" if policy == "default" else "bypassPermissions",
    ]
    if policy == "default":
        argv.extend(["--permission-prompts", "none"])
    else:
        argv.append("--allow-dangerously-skip-permissions")
    prompt = (
        f"{recipe.command}\n\n{recipe.request}"
        if recipe.command.startswith("/")
        else recipe.request
    )
    argv.extend(["--", prompt + "\n\nWork unattended. Do not wait for human input."])
    return argv


def display_command(argv: list[str]) -> str:
    return (
        subprocess.list2cmdline(argv) if sys.platform == "win32" else shlex.join(argv)
    )
