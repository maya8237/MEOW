"""Typed lint and tester command models that configured commands normalize into."""

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from meow.project.command_policy import split_command
from meow.project.config_files import CONFIG_FILENAME

DEFAULT_FIX_FLAG = "--fix"


@dataclass(frozen=True)
class LintCommand:
    """One lint command a project has configured.

    A `per_file` command runs as the generator's post-edit hook, on the single
    file just written. A `gate` command runs project-wide for the evaluator and
    its failure is a sprint FAIL; a non-gate command is non-blocking, which is
    what a whole-project analyzer needs when it is expected to report findings on
    pre-existing code.
    """

    command: str
    fix_flag: str | None = None
    per_file: bool = True
    gate: bool = True
    cwd: Path = Path(".")
    include: tuple[Path, ...] = ()
    exclude: tuple[Path, ...] = ()
    timeout: float | None = None
    env: dict[str, str] | None = None
    args: tuple[str, ...] = ()

    def argv(self) -> list[str]:
        """The command as argv, check-only, program resolved on PATH."""
        argv = split_command(self.command) + list(self.args)
        if not argv:
            raise ValueError(f"{CONFIG_FILENAME}: lint command is empty.")
        # Windows will not exec a .cmd shim (npx, eslint and oxlint all ship
        # as one) from a bare argv, so look the program up the way a shell
        # would. Falls back to the original name so a genuinely missing
        # program still surfaces as the OS error rather than being hidden.
        resolved = shutil.which(argv[0])
        # Native Windows executables run correctly through CreateProcess by
        # their bare name. Only resolve shell shims, which cannot be launched
        # directly by asyncio without their absolute path.
        if (
            resolved
            and os.name == "nt"
            and Path(resolved).suffix.lower()
            not in {
                ".bat",
                ".cmd",
            }
        ):
            resolved = argv[0]
        return [resolved or argv[0], *argv[1:]]

    def argv_for_file(self, file_path: str) -> list[str]:
        """The command as argv for one file, auto-fixing where supported."""
        argv = self.argv()
        if self.fix_flag:
            argv.append(self.fix_flag)
        argv.append(file_path)
        return argv

    def matches_file(self, file_path: Path) -> bool:
        """Whether a repo-relative file belongs to this command's component."""
        path = Path(os.path.normpath(str(file_path).replace("\\", "/")))
        cwd = self.cwd
        try:
            path.relative_to(cwd) if cwd != Path(".") else path
        except ValueError:
            return False
        if self.include and not any(
            _is_path_prefix(path, item) for item in self.include
        ):
            return False
        return not any(_is_path_prefix(path, item) for item in self.exclude)


@dataclass(frozen=True)
class VerificationCommand:
    cwd: Path
    command: str
    args: tuple[str, ...] = ()
    timeout: float = 300
    env: dict[str, str] | None = None
    gate: bool = True


@dataclass(frozen=True)
class DevServerCommand:
    cwd: Path
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] | None = None
    ready_url: str = ""
    startup_timeout: float = 30


def _is_path_prefix(path: Path, prefix: Path) -> bool:
    try:
        path.relative_to(prefix)
        return True
    except ValueError:
        return False


def resolve_command_cwd(active_dir: Path, relative: Path) -> Path:
    """Resolve a command cwd and ensure it exists inside the active checkout."""
    active = active_dir.resolve()
    candidate = (active / relative).resolve()
    try:
        candidate.relative_to(active)
    except ValueError as exc:
        raise ValueError(
            f"command cwd {relative!s} must resolve inside {active}"
        ) from exc
    if not candidate.is_dir():
        raise ValueError(f"command cwd {relative!s} does not exist as a directory")
    return candidate
