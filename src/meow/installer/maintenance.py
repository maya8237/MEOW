"""Shared, stdlib-only install/update steps; also runnable before installation."""

import argparse
import hashlib
import subprocess
import sys
import tomllib
from importlib.metadata import version
from pathlib import Path
from urllib.request import urlopen

PROJECT_URL = "https://raw.githubusercontent.com/maya8237/MEOW/main/pyproject.toml"
VERSION_COMPONENTS = 3


def _version_parts(value: str) -> tuple[int, ...]:
    parts = value.split(".")
    if len(parts) != VERSION_COMPONENTS or any(
        not part.isascii() or not part.isdecimal() for part in parts
    ):
        raise ValueError(f"Expected a three-part MEOW release version, got {value!r}")
    return tuple(int(part) for part in parts)


def checkout_version(repo: Path) -> str:
    with (repo / "pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)["project"]["version"]


def latest_version(*, timeout: float = 10) -> str:
    with urlopen(PROJECT_URL, timeout=timeout) as response:
        return tomllib.loads(response.read().decode("utf-8"))["project"]["version"]


def installed_checkout() -> Path:
    import meow

    for root in Path(meow.__file__).resolve().parents:
        if (root / ".git").exists() and (root / "skills").is_dir():
            return root
    raise RuntimeError(
        "Cannot locate the MEOW checkout. Rerun the installer to upgrade."
    )


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout.strip()


def update_checkout(repo: Path, *, reset: bool = False) -> None:
    if _git(repo, "status", "--porcelain"):
        raise RuntimeError(
            "The MEOW checkout has uncommitted changes; commit or stash them first."
        )
    branch = _git(repo, "branch", "--show-current")
    if branch not in {"", "main"}:
        raise RuntimeError("Switch the MEOW checkout to main before upgrading.")
    if not branch:
        _git(repo, "checkout", "main")
    if reset:
        _git(repo, "fetch", "origin", "main")
        _git(repo, "reset", "--hard", "FETCH_HEAD")
    else:
        _git(repo, "pull", "--ff-only", "origin", "main")


def install_checkout(repo: Path) -> None:
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", str(repo)],
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def run_setup(repo: Path) -> int:
    # A fresh interpreter loads the updated setup code rather than cached modules.
    return subprocess.run(
        [sys.executable, "-m", "meow.installer", "--repo-dir", str(repo)], check=False
    ).returncode


def check_for_updates() -> None:
    """Best-effort public-command warning; keep stdout and helper JSON clean."""
    try:
        latest = latest_version(timeout=1)
        try:
            current = checkout_version(installed_checkout())
        except RuntimeError:
            current = version("meow")
        newer = _version_parts(current) < _version_parts(latest)
    except (OSError, ValueError, RuntimeError, KeyError):
        return
    if newer:
        print(
            f"Warning: MEOW {latest} is available (current: {current}). "
            "Please update with `meow --update`.",
            file=sys.stderr,
        )


def _pending_file(repo: Path) -> Path:
    key = hashlib.sha256(str(repo).encode("utf-8")).hexdigest()
    return Path.home() / ".meow" / "updates" / f"{key}.pending"


def _complete_update(repo: Path, latest: str, *, pull: bool) -> int:
    if pull:
        update_checkout(repo)
    if _version_parts(checkout_version(repo)) < _version_parts(latest):
        raise RuntimeError(
            f"The checkout did not reach MEOW {latest}; inspect its main branch."
        )
    install_checkout(repo)
    result = run_setup(repo)
    if result == 0:
        _pending_file(repo).unlink(missing_ok=True)
        print(f"MEOW updated successfully (now {checkout_version(repo)}).")
    return result


def update() -> int:
    repo = installed_checkout()
    current, latest = checkout_version(repo), latest_version()
    pending = _pending_file(repo)
    pull = _version_parts(current) < _version_parts(latest)
    stale_package = _version_parts(version("meow")) < _version_parts(current)
    if not (pull or stale_package or pending.exists()):
        print(f"MEOW {current} is already up to date (origin/main: {latest}).")
        return 0
    print(f"Updating MEOW (checkout: {current}, origin/main: {latest})...")
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text(latest, encoding="utf-8")
    return _complete_update(repo, latest, pull=pull)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-dir", type=Path, required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--update", action="store_true")
    action.add_argument("--install", action="store_true")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    try:
        if args.update:
            update_checkout(args.repo_dir, reset=args.reset)
        else:
            install_checkout(args.repo_dir)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Installer failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
