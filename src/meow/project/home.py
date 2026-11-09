"""The home directory is the user scope, never a project."""

from pathlib import Path


def is_home_dir(path: Path | str) -> bool:
    """True when `path` is the user's home directory.

    `~/.meow` and `~/.claude` belong to the user scope there, so project and
    local scopes (and project onboarding, `.gitignore` edits) must sit out.
    """
    try:
        return Path(path).resolve() == Path.home().resolve()
    except (OSError, RuntimeError):
        return False
