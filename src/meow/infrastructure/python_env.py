"""Python environment setup commands: recognition, network defaults, repair."""

import os
import shutil
import subprocess
from pathlib import Path

ENV_COMMAND_TIMEOUT = 900.0
_INTERPRETER_PROBE_SECONDS = 60
_NETWORK_DEFAULTS = {
    "PIP_DEFAULT_TIMEOUT": "60",
    "PIP_RETRIES": "5",
    "UV_HTTP_TIMEOUT": "120",
    "UV_HTTP_RETRIES": "5",
    "POETRY_REQUESTS_TIMEOUT": "60",
}
_PYTHONS = {"python", "python3", "py"}


def _name(arg: str) -> str:
    base = Path(arg.replace("\\", "/")).name.lower()
    return base.removesuffix(".exe")


# Leading argv (after the program name) that marks an environment command.
_ENV_PREFIXES = {
    "python": [["-m", "venv"], ["-m", "virtualenv"], ["-m", "pip", "install"]],
    "uv": [["sync"], ["venv"], ["pip", "install"]],
    "pip": [["install"]],
    "poetry": [["install"]],
    "pdm": [["install"]],
    "hatch": [["env", "create"]],
    "virtualenv": [[]],
}


def _program(arg: str) -> str:
    name = _name(arg)
    if name in _PYTHONS or name.startswith("python3"):
        return "python"
    return "pip" if name == "pip3" else name


def is_env_command(argv: list[str]) -> bool:
    """True for commands that create or fill a Python environment."""
    prefixes = _ENV_PREFIXES.get(_program(argv[0]), []) if argv else []
    return any(argv[1 : 1 + len(prefix)] == prefix for prefix in prefixes)


def env_command_env(argv: list[str], env: dict[str, str]) -> dict[str, str]:
    """`env` plus network timeout defaults for environment commands."""
    if not is_env_command(argv):
        return dict(env)
    return {**_NETWORK_DEFAULTS, **env}


def _interpreter(path: Path) -> Path:
    scripts = path / "Scripts" / "python.exe"
    return scripts if scripts.exists() else path / "bin" / "python"


def venv_broken(path: Path) -> bool:
    """A venv without `pyvenv.cfg`, or whose interpreter exits non-zero.

    A probe that merely times out is a slow machine, not a broken venv."""
    if not (path / "pyvenv.cfg").is_file():
        return True
    try:
        result = subprocess.run(
            [str(_interpreter(path)), "-c", "pass"],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=_INTERPRETER_PROBE_SECONDS,
            check=False,
            env=dict(os.environ),
        )
    except subprocess.TimeoutExpired:
        return False
    except (OSError, subprocess.SubprocessError):
        return True
    return result.returncode != 0


def _tracked(worktree: Path, path: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "ls-files", "--", path.name],
            cwd=worktree,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return True  # cannot tell: keep it
    return result.returncode == 0 and bool(result.stdout.strip())


def repair_venvs(worktree: Path) -> list[Path]:
    """Remove each broken `.venv`/`venv` directly under the worktree that git
    does not track; return only the directories actually removed."""
    removed = []
    for name in (".venv", "venv"):
        path = worktree / name
        if not path.is_dir() or path.is_symlink() or not venv_broken(path):
            continue
        if _tracked(worktree, path):
            continue
        shutil.rmtree(path, ignore_errors=True)
        if path.exists():
            continue
        removed.append(path)
    return removed
