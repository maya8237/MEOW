"""Per-run test suppression and conservative automatic tester selection."""

import os
import subprocess
from pathlib import Path

from meow.project.config_files import SHARED_PROJECT_PATHS


def changed_paths(repo: Path) -> list[Path] | None:
    """Include staged, unstaged, untracked, renamed and deleted paths; fail closed."""
    try:
        done = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            cwd=repo,
            capture_output=True,
            check=False,
        )
        if done.returncode:
            return None
        entries = iter(done.stdout.split(b"\0"))
        paths = set()
        for entry in entries:
            if not entry:
                continue
            status = entry[:2]
            paths.add(Path(os.fsdecode(entry[3:])))
            if b"R" in status or b"C" in status:
                paths.add(Path(os.fsdecode(next(entries))))
        return sorted(
            path
            for path in paths
            if ".meow" not in path.parts
            or any(path.is_relative_to(shared) for shared in SHARED_PROJECT_PATHS)
        )
    except (OSError, StopIteration):
        return None


def behavior_changed(repo: Path) -> bool:
    """Only ordinary prose-only edits are low risk; uncertainty keeps the tester."""
    paths = changed_paths(repo)
    if not paths:
        return True
    return any(
        not (
            path.suffix.lower() in {".md", ".rst", ".txt"}
            and (
                path.parts[0] == "docs"
                or (
                    path.stem.upper() in {"README", "CHANGELOG", "LICENSE"}
                    and len(path.parts) == 1
                )
            )
        )
        for path in paths
    )


def disable_tests(config: dict) -> list[str]:
    """Filter runtime config only, leaving lint/build and on-disk config intact."""
    tester = config.get("tester", {})
    skipped = ["AI tester"]
    skipped.extend(
        f"test-{index}" for index, _ in enumerate(tester.get("tests", []), 1)
    )
    if tester.get("browser"):
        skipped.append("browser")
    config["tester"] = {**tester, "tests": [], "dev_server": [], "browser": None}
    config["_force_disable_tests"] = True
    return skipped
