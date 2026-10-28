"""Lazy, isolated Python dependencies for task worktrees."""

import hashlib
import os
import shlex
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

_MAX_REQUIREMENT_FILES = 128
_MAX_REQUIREMENT_BYTES = 1_000_000


def venv_python(root: Path) -> Path:
    return root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def python_environment(
    root: Path, overrides: dict[str, str] | None = None, *, inherit: bool = True
) -> dict[str, str]:
    """Route child commands through a local venv without changing os.environ."""
    env = dict(os.environ) if inherit else {}
    executable = venv_python(root.resolve())
    if executable.is_file() and (root / ".venv/pyvenv.cfg").is_file():
        env["VIRTUAL_ENV"] = str(executable.parent.parent)
        env["PATH"] = str(executable.parent) + os.pathsep + os.environ.get("PATH", "")
        env.pop("PYTHONHOME", None)
        if not inherit:
            env["PYTHONHOME"] = ""  # the SDK merges os.environ before options.env
    env.update(overrides or {})
    return env


def run_captured(*args):
    # checks imports test_runner, which consumes python_environment.
    from meow.infrastructure.checks import run_captured as capture

    return capture(*args)


def _manifest(root: Path) -> dict:
    from meow.infrastructure.worktree_setup import WorktreeSetupError

    path = root / "pyproject.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if any(
            not isinstance(data.get(key, {}), dict)
            for key in ("project", "build-system", "dependency-groups")
        ):
            raise ValueError("expected tables")
        return data
    except (OSError, ValueError) as exc:
        raise WorktreeSetupError("Cannot read Python project manifest") from exc


def _group_requirements(
    groups: dict, name: str, seen: tuple[str, ...] = ()
) -> list[str]:
    from meow.infrastructure.worktree_setup import WorktreeSetupError

    if name in seen or not isinstance(groups.get(name), list):
        raise WorktreeSetupError("Invalid or cyclic Python dependency group")
    requirements = []
    for item in groups[name]:
        if isinstance(item, str) and item and not item.startswith("-"):
            requirements.append(item)
        elif isinstance(item, dict) and isinstance(item.get("include-group"), str):
            requirements.extend(
                _group_requirements(groups, item["include-group"], (*seen, name))
            )
        else:
            raise WorktreeSetupError("Invalid Python dependency group entry")
    return requirements


def _commands(root: Path, manifest: dict) -> list[list[str]]:
    project = manifest.get("project", {})
    optional = project.get("optional-dependencies", {})
    extra = isinstance(optional, dict) and "dev" in optional
    uv = shutil.which("uv")
    if (root / "uv.lock").is_file() and manifest and uv:
        return [[uv, "sync", "--locked", *(["--extra", "dev"] if extra else [])]]
    return _pip_commands(root, manifest, extra=extra)


def _pip_commands(root: Path, manifest: dict, *, extra: bool) -> list[list[str]]:
    executable = str(venv_python(root))
    pip = [
        executable,
        "-m",
        "pip",
        "install",
        "--prefix",
        str(root / ".venv"),
        "--no-user",
    ]
    installs = []
    if manifest.get("project", {}).get("name") or "build-system" in manifest:
        installs.append([*pip, "-e", ".[dev]" if extra else "."])
    for name in ("requirements.txt", "requirements-dev.txt"):
        if (root / name).is_file():
            installs.append([*pip, "-r", name])
    if "dev" in manifest.get("dependency-groups", {}):
        requirements = _group_requirements(manifest["dependency-groups"], "dev")
        if requirements:
            installs.append([*pip, *requirements])
    return (
        [[sys.executable, "-m", "venv", str(root / ".venv")], *installs]
        if installs
        else []
    )


def _requirement_includes(content: bytes) -> list[str] | None:
    try:
        lines = (
            content
            .decode("utf-8-sig")
            .replace("\\\r\n", "")
            .replace("\\\n", "")
            .splitlines()
        )
        includes = []
        for line in lines:
            words = iter(shlex.split(line, comments=True))
            for word in words:
                option, equals, value = word.partition("=")
                if option in {"-r", "-c", "--requirement", "--constraint"}:
                    includes.append(value if equals else next(words))
                elif word.startswith(("-r", "-c")):
                    includes.append(word[2:])
        return includes
    except (UnicodeError, ValueError, StopIteration):
        return None


def _requirement_contents(root: Path) -> dict[Path, bytes] | None:
    pending = list(root.glob("requirements*.txt"))
    contents: dict[Path, bytes] = {}
    try:
        while pending:
            path = pending.pop().resolve()
            if path in contents:
                continue
            if (
                not path.is_relative_to(root)
                or len(contents) >= _MAX_REQUIREMENT_FILES
                or path.stat().st_size > _MAX_REQUIREMENT_BYTES
            ):
                return None
            content = path.read_bytes()
            includes = _requirement_includes(content)
            if includes is None or any(
                not value or "://" in value or "$" in value for value in includes
            ):
                return None
            contents[path] = content
            pending.extend(path.parent / value for value in includes)
        return contents
    except (OSError, RuntimeError):
        return None


def _fingerprint(root: Path, commands: list[list[str]]) -> str | None:
    digest = hashlib.sha256(repr((str(root), commands)).encode())
    contents = _requirement_contents(root)
    if contents is None:
        return None
    for path in (root / "pyproject.toml", root / "uv.lock"):
        if path.is_file():
            contents[path] = path.read_bytes()
    for path, content in sorted(contents.items()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(content)
    return digest.hexdigest()


def _install(
    root: Path, argv: list[str], timeout: float, record_action: Callable | None
) -> None:
    from meow.infrastructure.worktree_setup import WorktreeSetupError

    env = python_environment(root)
    env.pop("PYTHONHOME", None)
    for name in ("PIP_TARGET", "PIP_PREFIX", "PIP_USER", "PIP_ROOT", "PIP_PYTHON"):
        env.pop(name, None)
    env["PIP_CONFIG_FILE"] = os.devnull
    env["UV_PROJECT_ENVIRONMENT"] = str(root / ".venv")
    # Never reuse a caller's activated environment or let uv update the toolchain.
    env["UV_PYTHON_DOWNLOADS"] = "never"
    try:
        code, _output, timed_out = run_captured(argv, root, env, timeout)
    except (OSError, ValueError) as exc:
        if record_action:
            record_action({"kind": "python-dependencies", "status": "failed"})
        raise WorktreeSetupError("Python dependency command could not start") from exc
    if record_action:
        record_action({
            "kind": "python-dependencies",
            "cwd": str(root),
            "status": "passed" if code == 0 and not timed_out else "failed",
            "exit_code": code,
            "timed_out": timed_out,
        })
    if code != 0 or timed_out:
        reason = "timed out" if timed_out else f"exit {code}"
        raise WorktreeSetupError(
            f"Python dependency preparation failed ({reason}); rerun MEOW"
        )


def _ignore_environment(root: Path) -> None:
    """Use Git's local excludes; never edit a tracked project .gitignore."""
    from meow.infrastructure.worktree_setup import WorktreeSetupError

    if not (root / ".git").exists():
        return
    try:
        ignored = subprocess.run(
            ["git", "check-ignore", "--quiet", "--no-index", ".venv/"],
            cwd=root,
            capture_output=True,
            check=False,
            timeout=10,
        )
        if ignored.returncode == 0:
            return
        location = subprocess.run(
            ["git", "rev-parse", "--git-path", "info/exclude"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        path = root / location.stdout.strip()
        previous = path.read_text(encoding="utf-8") if path.exists() else ""
        path.parent.mkdir(parents=True, exist_ok=True)
        separator = "" if not previous or previous.endswith("\n") else "\n"
        path.write_text(previous + separator + "/.venv/\n", encoding="utf-8")
    except (OSError, subprocess.SubprocessError) as exc:
        raise WorktreeSetupError(
            "Cannot exclude the worktree Python environment from Git"
        ) from exc


def _environment_ready(root: Path) -> bool:
    return venv_python(root).is_file() and (root / ".venv/pyvenv.cfg").is_file()


def ensure_python_dependencies(  # ruff: ignore[complex-structure, too-many-statements]
    root: Path,
    *,
    command_decision: Callable[[list[str]], str | None] | None = None,
    record_action: Callable[[dict[str, object]], None] | None = None,
    timeout: float | None = None,
) -> None:
    """Prepare a recognized project; caller must supply an isolated task worktree."""
    from meow.infrastructure.worktree_setup import WorktreeSetupError, _no_symlinks

    root = root.resolve()
    commands = _commands(root, _manifest(root))
    if not commands:
        return
    _no_symlinks(root, Path(".venv"))
    _no_symlinks(root, venv_python(root).parent.relative_to(root))
    _no_symlinks(root, Path(".venv/pyvenv.cfg"))
    fingerprint = _no_symlinks(root, Path(".meow/python-install.fingerprint"))
    pending = _no_symlinks(root, Path(".meow/python-install.pending"))
    expected = _fingerprint(root, commands)
    if (
        expected is not None
        and not pending.exists()
        and _environment_ready(root)
        and fingerprint.is_file()
        and fingerprint.read_text(encoding="utf-8") == expected
    ):
        return
    if command_decision and any(
        command_decision(argv) in {"deny", "ask"} for argv in commands
    ):
        raise WorktreeSetupError(
            "Python dependency preparation requires approval or is denied by policy"
        )
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.touch()
    _ignore_environment(root)
    for argv in commands:
        _install(root, argv, timeout or 900.0, record_action)
    if not _environment_ready(root):
        raise WorktreeSetupError(
            "Python dependency preparation left an incomplete .venv"
        )
    if expected is None:
        fingerprint.unlink(missing_ok=True)
    else:
        fingerprint.write_text(expected, encoding="utf-8")
    pending.unlink()
