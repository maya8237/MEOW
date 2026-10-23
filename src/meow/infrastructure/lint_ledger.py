"""Content-bound evidence shared by Claude Code and SDK lint hooks."""

import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path

from meow.infrastructure.verification_policy import changed_paths

LEDGER = Path(".meow/lint-ledger.jsonl")


def content_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def lint_fingerprint(commands, timeout: float) -> str:
    payload = json.dumps(
        [asdict(entry) for entry in commands] + [timeout], default=str, sort_keys=True
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def record_lint(repo: Path, commands, timeout: float, path: Path, *, ok: bool) -> None:  # ruff: ignore[too-many-arguments] -- file proof includes its effective lint config
    """An evidence write failure must never prevent the edit's lint feedback."""
    try:
        relative = path.resolve().relative_to(repo.resolve())
        entry = {
            "path": relative.as_posix(),
            "sha256": content_hash(path),
            "ok": ok,
            "timestamp": time.time(),
            "config": lint_fingerprint(commands, timeout),
        }
        ledger = repo / LEDGER
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry) + "\n")
    except (OSError, ValueError):
        return


def verified_lint(repo: Path, config: dict) -> list[str] | None:  # ruff: ignore[complex-structure, too-many-return-statements, too-many-branches] -- all uncertainty rejects reuse
    """Return covered changed files only when all per-file lint is proven."""
    from meow.hooks.claude import inspect_claude_hooks

    try:
        installed = inspect_claude_hooks(repo)
        commands = config["lint"]
        if (
            installed.get("_diagnostic")
            or installed.get("lint") != "active"
            or not commands
        ):
            return None
        if any(not entry.per_file for entry in commands):
            return None
        paths = changed_paths(repo)
        if not paths:
            return None
        latest = {}
        for line in (repo / LEDGER).read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            latest[entry["path"]] = entry
        fingerprint = lint_fingerprint(commands, config["lint_timeout"])
        covered = []
        for path in paths:
            if not any(entry.matches_file(path) for entry in commands):
                continue
            evidence = latest.get(path.as_posix(), {})
            if (
                evidence.get("ok") is not True
                or evidence.get("config") != fingerprint
                or evidence.get("sha256") != content_hash(repo / path)
            ):
                return None
            covered.append(path.as_posix())
        return covered or None
    except (OSError, ValueError, KeyError, TypeError):
        return None
