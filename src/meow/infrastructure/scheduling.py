"""Durable one-shot schedules shared by the CLI, plugin skills, and local UI."""

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import override
from uuid import uuid4

from meow.infrastructure import scheduled_tasks
from meow.ui.sessions import Recipe, _atomic_json, _file_transaction, command_for

_unit_dir = scheduled_tasks.unit_dir
_ID = re.compile(r"[0-9a-f]{32}\Z")


def _next_clock(value: str, now: datetime, *, local: bool) -> datetime:
    hour, minute = map(int, value.split(":"))
    at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if at <= now:
        at += timedelta(days=1)
    # Resolve the target date's local DST offset, not today's fixed offset.
    return at.replace(tzinfo=None).astimezone() if local else at


def parse_at(value: str, *, now: datetime | None = None) -> datetime:
    """Resolve a local ISO timestamp or next HH:MM occurrence to UTC."""
    local = now is None
    now = now or datetime.now().astimezone()
    if not isinstance(value, str) or not value.strip():
        raise ValueError("--at must be an ISO timestamp or HH:MM local time")
    try:
        if re.fullmatch(r"\d{2}:\d{2}", value):
            at = _next_clock(value, now, local=local)
        else:
            if "T" not in value and " " not in value:
                raise ValueError("Include both a date and a time")
            at = datetime.fromisoformat(value).astimezone()
    except ValueError as exc:
        raise ValueError("--at must be an ISO timestamp or HH:MM local time") from exc
    at = at.astimezone(UTC).replace(microsecond=0)
    if at <= now.astimezone(UTC):
        raise ValueError("Schedule time must be in the future")
    return at


def _path(repo: Path, schedule_id: str) -> Path:
    if not isinstance(schedule_id, str) or not _ID.fullmatch(schedule_id):
        raise ValueError("Invalid schedule identifier")
    return repo.resolve() / ".meow" / "schedules" / f"{schedule_id}.json"


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("Schedule not found") from exc


class _ScheduleParser(argparse.ArgumentParser):
    @override
    def error(self, message):
        raise ValueError(message)


def _run_argv(repo: Path, argv: list[str]) -> list[str]:
    argv = argv[1:] if argv[:1] == ["--"] else list(argv)
    if not argv or argv[0] != "run":
        raise ValueError("Schedule a run: meow schedule add --at TIME -- run ...")
    argv = ["run", "--unattended", "--work-dir", str(repo), *argv[1:]]
    _validate_run(repo, argv)
    return [sys.executable, "-u", "-m", "meow", *argv]


def _validate_run(repo: Path, argv: list[str]) -> None:
    from meow.cli.parser import _build_arg_parser
    from meow.cli.validation import (
        _validate_feature_name_requirement,
        _validate_run_flags,
    )

    parser = _build_arg_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        raise ValueError("Invalid scheduled run arguments") from exc
    validator = _ScheduleParser()
    if Path(args.working_dir).resolve() != repo or not args.unattended:
        raise ValueError(
            "Scheduled runs require --unattended and the selected work-dir"
        )
    _validate_run_flags(validator, args)
    _validate_feature_name_requirement(validator, args, repo)
    if args.background or args.source_branch:
        raise ValueError("Scheduled runs cannot use --background or --from")


def schedule_run(repo: Path, at: str, argv: list[str]) -> dict:
    repo = repo.resolve()
    return _register(repo, at, {"argv": _run_argv(repo, argv)})


def schedule_session(repo: Path, at: str, recipe: Recipe) -> dict:
    argv = command_for(recipe)
    if recipe.mode == "cli" and recipe.command == "run":
        # Validate the effective flags, including advanced arguments.
        _validate_run(
            repo.resolve(), [argv[4], "--work-dir", str(repo.resolve()), *argv[5:]]
        )
    return _register(
        repo.resolve(),
        at,
        {"argv": argv, "session_id": recipe.id, "recipe": asdict(recipe)},
    )


def _register(repo: Path, at: str, payload: dict) -> dict:
    when = parse_at(at)
    if sys.platform not in {"win32", "linux"}:
        raise ValueError("Scheduling supports Windows Task Scheduler and Linux systemd")
    schedule_id = uuid4().hex
    path = _path(repo, schedule_id)
    job = {
        "id": schedule_id,
        "repo": str(repo),
        "at": when.isoformat(),
        "backend": "windows" if sys.platform == "win32" else "systemd",
        "state": "registering",
        "created_at": datetime.now(UTC).isoformat(),
        "path": os.environ.get("PATH", ""),
        "worker_argv": [
            sys.executable,
            "-m",
            "meow",
            "_scheduled",
            schedule_id,
            "--work-dir",
            str(repo),
        ],
        **payload,
    }
    # Registration and execution share a short lock so an imminent trigger
    # cannot consume an incomplete record or have its result overwritten.
    with _file_transaction(path.with_suffix(".lock"), "schedule"):
        _atomic_json(path, job)
        try:
            if job["backend"] == "windows":
                scheduled_tasks.register_windows(job, path.with_suffix(".xml"))
            else:
                scheduled_tasks.register_linux(job, _unit_dir())
        except (ValueError, OSError) as exc:
            job.update(state="failed", error=str(exc))
            _atomic_json(path, job)
            raise
        job["state"] = "scheduled"
        _atomic_json(path, job)
    return job


def list_schedules(repo: Path) -> list[dict]:
    directory = repo.resolve() / ".meow" / "schedules"
    return sorted(
        (_read(path) for path in directory.glob("*.json")),
        key=lambda job: job["at"],
    )


def cancel_schedule(repo: Path, schedule_id: str) -> dict:
    path = _path(repo, schedule_id)
    with _file_transaction(path.with_suffix(".lock"), "schedule"):
        job = _read(path)
        if job["state"] == "running":
            raise ValueError(
                "Schedule already started; use the run/session stop controls"
            )
        if not job.get("trigger_removed"):
            scheduled_tasks.unregister(job, _unit_dir())
            job["trigger_removed"] = True
            if job["state"] == "scheduled":
                job["state"] = "cancelled"
            _atomic_json(path, job)
    return job


def _execute(job: dict, repo: Path, log: Path) -> int:
    if "session_id" in job:
        from meow.infrastructure.ui_sessions import launch_attempt

        os.environ["PATH"] = job["path"]
        attempt = launch_attempt(
            repo, job["session_id"], recipe=Recipe(**job["recipe"])
        )
        job.update(attempt_id=attempt.id, state="launched")
        return 0
    with log.open("ab") as stream:
        result = subprocess.run(
            job["argv"],
            cwd=repo,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PATH": job["path"]},
            check=False,
        )
    job["state"] = "succeeded" if result.returncode == 0 else "failed"
    return result.returncode


def execute_schedule(repo: Path, schedule_id: str) -> int:
    """Claim once, then execute outside the metadata lock. Never prompt."""
    repo = repo.resolve()
    path = _path(repo, schedule_id)
    # Native registration can take two 30-second commands, plus rollback.
    # An imminent trigger must wait until that transaction publishes its result.
    with _file_transaction(path.with_suffix(".lock"), "schedule", timeout=120):
        job = _read(path)
        if job["state"] != "scheduled":
            return 0
        job.update(state="running", started_at=datetime.now(UTC).isoformat())
        _atomic_json(path, job)
    log = path.with_suffix(".log")
    try:
        code = _execute(job, repo, log)
    except Exception as exc:
        job.update(state="failed", error=str(exc))
        code = 1
    job.update(exit_code=code, finished_at=datetime.now(UTC).isoformat(), log=str(log))
    _atomic_json(path, job)
    return code
