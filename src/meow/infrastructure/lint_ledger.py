"""Content-bound evidence shared by Claude Code and SDK lint hooks."""

import hashlib
import json
import subprocess
import time
from dataclasses import asdict
from pathlib import Path

from meow.infrastructure.verification_policy import changed_paths

LEDGER = Path(".meow/lint-ledger.jsonl")
COMPACT_BYTES = 256 * 1024


def _head(repo: Path) -> str | None:
    done = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    return done.stdout.strip() if done.returncode == 0 else None


def content_hash(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def lint_fingerprint(commands, timeout: float) -> str:
    payload = json.dumps(
        [asdict(entry) for entry in commands] + [timeout], default=str, sort_keys=True
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _compact(repo: Path, ledger: Path) -> None:
    """Keep only the newest entry per existing file; reuse reads nothing else."""
    latest = {}
    for line in ledger.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        if (repo / entry["path"]).exists():
            latest[entry["path"]] = line
    temp = ledger.with_suffix(".tmp")
    temp.write_text("".join(f"{line}\n" for line in latest.values()), encoding="utf-8")
    temp.replace(ledger)


def record_lint(repo: Path, commands, timeout: float, path: Path, *, ok: bool) -> None:  # ruff: ignore[too-many-arguments] -- file proof includes its effective lint config
    """An evidence write failure must never prevent the edit's lint feedback."""
    try:
        relative = path.resolve().relative_to(repo.resolve())
        digest = content_hash(path)
        entry = {
            "path": relative.as_posix(),
            "sha256": digest,
            "ok": ok and digest is not None,
            "timestamp": time.time(),
            "config": lint_fingerprint(commands, timeout),
            "head": _head(repo),
        }
        ledger = repo / LEDGER
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry) + "\n")
        if ledger.stat().st_size > COMPACT_BYTES:
            _compact(repo, ledger)
    except (OSError, ValueError, KeyError):
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
        head = _head(repo)
        if not paths or not head:
            return None
        # Unmatched edits can change linter rules, imports, or plugin behavior.
        if any(
            not any(entry.matches_file(path) for entry in commands) for path in paths
        ):
            return None
        latest = {}
        for entry in map(
            json.loads, (repo / LEDGER).read_text(encoding="utf-8").splitlines()
        ):
            latest[entry["path"]] = entry
        fingerprint = lint_fingerprint(commands, config["lint_timeout"])
        covered = []
        for path in paths:
            evidence = latest.get(path.as_posix(), {})
            digest = content_hash(repo / path)
            if (
                evidence.get("ok") is not True
                or evidence.get("head") != head
                or evidence.get("config") != fingerprint
                or digest is None
                or evidence.get("sha256") != digest
            ):
                return None
            covered.append(path.as_posix())
        return covered or None
    except (OSError, ValueError, KeyError, TypeError):
        return None
