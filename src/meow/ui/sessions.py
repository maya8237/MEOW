"""Validated launch recipes for the local UI sessions API."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypedDict

from meow.cli.parser import _build_arg_parser
from meow.cli.review_cli import _validate_review_flags
from meow.cli.validation import (
    _validate_ci_flags,
    _validate_feature_name_requirement,
    _validate_run_flags,
)
from meow.common.process import process_identity


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
    errors = io.StringIO()
    try:
        with contextlib.redirect_stderr(errors):
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
        details = [line for line in errors.getvalue().splitlines() if line.strip()]
        message = details[-1] if details else "Invalid MEOW command arguments"
        raise RecipeError(message) from exc
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


def _builtin_skill_names() -> set[str]:
    skill_root = Path(__file__).resolve().parents[3] / "skills"
    if not skill_root.is_dir():
        return set()
    return {
        directory.name
        for directory in skill_root.iterdir()
        if directory.is_dir() and (directory / "SKILL.md").is_file()
    }


def _custom_skill_names(repo: Path) -> set[str]:
    from meow.project.config import load_config

    return {
        skill.qualified_name for skill in load_config(repo)["customizations"].skills
    }


def _supported_slash_skills(repo: Path) -> set[str]:
    try:
        builtin = {f"/meow:{name}" for name in _builtin_skill_names()}
        custom = {f"/{name}" for name in _custom_skill_names(repo)}
    except (OSError, ValueError) as exc:
        raise RecipeError(
            f"Could not resolve the selected repository skill catalog: {exc}"
        ) from exc
    return builtin | custom


def _validate_claude_command(repo: Path, command: str) -> None:
    if command == "prompt":
        return
    if command.startswith("/") and command in _supported_slash_skills(repo):
        return
    raise RecipeError(
        "supported Claude command is 'prompt' or a resolvable slash skill"
    )


def _validate_mode_recipe(recipe: Recipe) -> None:
    if recipe.mode == "cli":
        command_for(recipe)
        return
    _validate_claude_recipe(recipe)


def _validate_claude_recipe(recipe: Recipe) -> None:
    if recipe.workflow is not None or recipe.inputs or recipe.arguments:
        raise RecipeError(
            "Claude recipes do not accept CLI workflow, input, or argument fields"
        )
    _validate_claude_command(Path(recipe.repo), recipe.command)


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
    _validate_mode_recipe(recipe)
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
    if recipe.options.get("unattended", True):
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
    _validate_claude_recipe(recipe)
    policy = recipe.options.get("permission_policy", "default")
    if policy not in {"default", "bypass"}:
        raise RecipeError("permission_policy must be 'default' or 'bypass'")
    capabilities = detect_capabilities(Path(recipe.repo))
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


_SESSION_ID = re.compile(r"[0-9a-f]{32}\Z")
_ATTEMPT_STATES = {
    "starting",
    "running",
    "stopping",
    "succeeded",
    "failed",
    "stopped",
    "interrupted",
    "needs_decision",
}
_ACTIVE_STATES = {"starting", "running", "stopping"}
_IMMUTABLE_ATTEMPT_FIELDS = {
    "id",
    "session_id",
    "created_at",
    "effective_recipe",
    "output_file",
}
_OUTPUT_LIMIT = 64 * 1024
_OUTPUT_TAIL = 16 * 1024
_UTF8_CONTINUATION = 0x80


class SessionBusy(RuntimeError):
    """Another process owns a session operation."""


class SessionError(ValueError):
    """Session data is missing or invalid."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Attempt:
    id: str
    state: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    argv: list[str]
    effective_recipe: dict
    worker: dict
    exit_code: int | None
    output_file: str
    claude_session_id: str | None
    run_ids: list[str]
    error: str | None
    session_id: str
    output_size: int = 0


@dataclass
class Session:
    id: str
    recipe: dict
    created_at: str
    updated_at: str
    state: str
    attempts: list[str]
    archived: bool = False


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


class SessionStore:  # ruff: ignore[too-many-public-methods]
    """Atomic session records and per-attempt append-only output."""

    def __init__(self, repo: Path):
        self.repo = Path(repo).resolve()
        self.directory = self.repo / ".meow" / "ui-sessions"

    @staticmethod
    def _session_dir_for(repo: Path, session_id: str) -> Path:
        if not _SESSION_ID.fullmatch(session_id):
            raise SessionError("Invalid session id")
        return Path(repo).resolve() / ".meow" / "ui-sessions" / session_id

    def _directory(self, session_id: str) -> Path:
        return self._session_dir_for(self.repo, session_id)

    @staticmethod
    def _attempt_from_json(data: dict) -> Attempt:
        return Attempt(**data)

    def _read_session(self, session_id: str) -> Session:
        path = self._directory(session_id) / "session.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("id") != session_id:
                raise SessionError("Session id does not match its file")
            session = Session(**data)
            if (
                not isinstance(session.recipe, dict)
                or session.recipe.get("id") != session_id
                or Path(session.recipe.get("repo", "")).resolve() != self.repo
                or not isinstance(session.attempts, list)
                or any(not _SESSION_ID.fullmatch(item) for item in session.attempts)
            ):
                raise SessionError("Session data is invalid")
            return session
        except (OSError, ValueError, TypeError) as exc:
            if isinstance(exc, SessionError):
                raise
            raise SessionError(f"Could not load session {session_id}") from exc

    def list(self) -> list[Session]:
        sessions = []
        if self.directory.is_dir():
            for path in self.directory.glob("*/session.json"):
                try:
                    sessions.append(self._read_session(path.parent.name))
                except SessionError:
                    continue
        return sorted(sessions, key=lambda item: item.updated_at, reverse=True)

    def load(self, session_id: str) -> Session:
        return self._read_session(session_id)

    def _save_session(self, session: Session) -> None:
        session.updated_at = _now()
        _atomic_json(self._directory(session.id) / "session.json", asdict(session))

    def save_recipe(self, recipe: Recipe) -> Session:
        if (
            not _SESSION_ID.fullmatch(recipe.id)
            or Path(recipe.repo).resolve() != self.repo
        ):
            raise SessionError("Recipe id or repository is invalid")
        directory = self._directory(recipe.id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "session.json"
        if path.exists():
            raise SessionError("Session already exists")
        session = Session(recipe.id, recipe_json(recipe), _now(), _now(), "draft", [])
        _atomic_json(path, asdict(session))
        return session

    def update_recipe(self, session_id: str, recipe: Recipe) -> Session:
        if Path(recipe.repo).resolve() != self.repo:
            raise SessionError("Recipe repository does not match this session")
        session = self._read_session(session_id)
        session.recipe = recipe_json(replace(recipe, id=session_id))
        session.state = "draft"
        self._save_session(session)
        return session

    def _read_attempt(self, session_id: str, attempt_id: str) -> Attempt:
        if not _SESSION_ID.fullmatch(attempt_id):
            raise SessionError("Invalid attempt id")
        path = self._directory(session_id) / f"{attempt_id}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            attempt = self._attempt_from_json(data)
            if attempt.id != attempt_id or attempt.session_id != session_id:
                raise SessionError("Attempt identity does not match its file")
            if (
                attempt.output_file != f"{attempt_id}.output"
                or attempt.state not in _ATTEMPT_STATES
                or not isinstance(attempt.worker, dict)
            ):
                raise SessionError("Attempt data is invalid")
            return attempt
        except (OSError, ValueError, TypeError) as exc:
            if isinstance(exc, SessionError):
                raise
            raise SessionError(f"Could not load attempt {attempt_id}") from exc

    @contextmanager
    def claim(  # ruff: ignore[complex-structure, too-many-branches, too-many-statements]
        self, session_id: str, action: str
    ):
        """Acquire a recoverable, birth-identity-checked session operation lock."""
        directory = self._directory(session_id)
        directory.mkdir(parents=True, exist_ok=True)
        lock = directory / "claim.json"
        token = new_id()
        identity = process_identity(os.getpid())
        if not identity:
            raise SessionError("Could not identify session lock owner")
        data = {
            "token": token,
            "pid": os.getpid(),
            "identity": identity,
            "created_at": time.time(),
            "action": action,
        }
        for _ in range(2):
            try:
                descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                try:
                    lock_stat = lock.stat()
                    old = json.loads(lock.read_text(encoding="utf-8"))
                    expected = old.get("identity")
                    alive = bool(expected) and process_identity(old["pid"]) == expected
                except (OSError, ValueError, KeyError, TypeError):
                    lock_stat = None
                    alive = False
                if alive:
                    raise SessionBusy(
                        "Session operation is already in progress"
                    ) from None
                try:
                    current_stat = lock.stat()
                    if lock_stat and (current_stat.st_dev, current_stat.st_ino) == (
                        lock_stat.st_dev,
                        lock_stat.st_ino,
                    ):
                        lock.unlink()
                except FileNotFoundError:
                    pass
                continue
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                yield token
            finally:
                try:
                    current = json.loads(lock.read_text(encoding="utf-8"))
                    if current.get("token") == token:
                        lock.unlink(missing_ok=True)
                except (OSError, ValueError):
                    pass
            return
        raise SessionBusy("Could not acquire session operation lock")

    def create_attempt(
        self, session_id: str, effective_recipe: Recipe, worker_token: str
    ) -> Attempt:
        session = self._read_session(session_id)
        if any(
            self._read_attempt(session_id, item).state in _ACTIVE_STATES
            for item in session.attempts
        ):
            raise SessionBusy("Session already has an active attempt")
        attempt_id = new_id()
        attempt = Attempt(
            attempt_id,
            "starting",
            _now(),
            None,
            None,
            [],
            recipe_json(effective_recipe),
            {"pid": None, "identity": None, "token": worker_token},
            None,
            f"{attempt_id}.output",
            None,
            [],
            None,
            session_id,
        )
        _atomic_json(
            self._directory(session_id) / f"{attempt_id}.json", asdict(attempt)
        )
        session.attempts.append(attempt_id)
        session.state = "starting"
        self._save_session(session)
        return attempt

    def update_attempt(self, session_id: str, attempt_id: str, **changes) -> Attempt:
        attempt = self._read_attempt(session_id, attempt_id)
        for key, value in changes.items():
            if key in _IMMUTABLE_ATTEMPT_FIELDS:
                raise SessionError(f"Attempt field cannot be changed: {key}")
            if not hasattr(attempt, key):
                raise SessionError(f"Unsupported attempt field: {key}")
            setattr(attempt, key, value)
        _atomic_json(
            self._directory(session_id) / f"{attempt_id}.json", asdict(attempt)
        )
        session = self._read_session(session_id)
        if attempt.state in _ATTEMPT_STATES:
            session.state = attempt.state
        self._save_session(session)
        return attempt

    def finish_attempt(
        self, session_id: str, attempt_id: str, state: str, exit_code: int | None
    ) -> Attempt:
        if state not in _ATTEMPT_STATES - _ACTIVE_STATES:
            raise SessionError("Invalid terminal attempt state")
        return self.update_attempt(
            session_id, attempt_id, state=state, exit_code=exit_code, finished_at=_now()
        )

    def append_output(self, session_id: str, attempt_id: str, data: bytes) -> None:
        self._read_attempt(session_id, attempt_id)
        path = self._directory(session_id) / f"{attempt_id}.output"
        with path.open("ab") as stream:
            stream.write(data)
            stream.flush()
        self.update_attempt(session_id, attempt_id, output_size=path.stat().st_size)

    def append_raw(self, session_id: str, attempt_id: str, data: bytes) -> None:
        self._read_attempt(session_id, attempt_id)
        with (self._directory(session_id) / f"{attempt_id}.raw").open("ab") as stream:
            stream.write(data)
            stream.flush()

    def read_output(  # ruff: ignore[too-many-statements]
        self, session_id: str, attempt_id: str, cursor: int, limit: int
    ) -> dict:
        self._read_attempt(session_id, attempt_id)
        path = self._directory(session_id) / f"{attempt_id}.output"
        path.touch(exist_ok=True)
        stat = path.stat()
        saved = self._read_attempt(session_id, attempt_id)
        marker = saved.worker.get("output_identity")
        identity = f"{stat.st_dev}:{stat.st_ino}"
        reset = bool(
            marker
            and (
                marker != identity or stat.st_size < saved.worker.get("output_size", 0)
            )
        )
        cursor = max(0, int(cursor))
        if reset:
            cursor = max(0, stat.st_size - _OUTPUT_TAIL)
        cursor = min(cursor, stat.st_size)
        limit = min(max(int(limit), 1), _OUTPUT_LIMIT)
        with path.open("rb") as stream:
            while cursor and cursor < stat.st_size:
                stream.seek(cursor)
                if stream.read(1)[0] & 0xC0 != _UTF8_CONTINUATION:
                    break
                cursor -= 1
            stream.seek(cursor)
            data = stream.read(min(limit + 4, stat.st_size - cursor))
        data = data[:limit]
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            if exc.reason == "unexpected end of data":
                data = data[: exc.start]
            text = data.decode("utf-8", errors="replace")
        next_cursor = cursor + len(data)
        self.update_attempt(
            session_id,
            attempt_id,
            worker={
                **saved.worker,
                "output_identity": identity,
                "output_size": stat.st_size,
            },
            output_size=stat.st_size,
        )
        return {
            "text": text,
            "next_cursor": next_cursor,
            "size": stat.st_size,
            "truncated": next_cursor < stat.st_size,
            "reset": reset,
        }

    def archive(self, session_id: str, archived: bool) -> Session:
        session = self._read_session(session_id)
        if any(
            self._read_attempt(session_id, item).state in _ACTIVE_STATES
            for item in session.attempts
        ):
            raise SessionBusy("Cannot archive a session with an active attempt")
        session.archived = bool(archived)
        return self._save_and_return(session)

    def _save_and_return(self, session: Session) -> Session:
        self._save_session(session)
        return session
