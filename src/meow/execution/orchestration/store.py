"""Atomic orchestrator journal and the single-driver lock."""

import json
import os
import socket
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from meow.common.process import process_identity
from meow.execution.orchestration.model import (
    MODES,
    PHASES,
    SCHEMA_VERSION,
    SOURCES,
    TASK_STATUSES,
    OrchestratorRecord,
    OrchestratorTask,
    is_orchestrator_id,
    new_orchestrator_id,
)
from meow.execution.queue_state import _pid_is_alive

NATIVE_HEARTBEAT_SECONDS = 600
# A lock file with no readable owner after this long was left by a driver that
# died between creating the file and writing it.
_UNREADABLE_LOCK_GRACE_SECONDS = 30
STATE_FILE = "state.json"
LOCK_FILE = "driver.lock"


class OrchestratorStateError(ValueError):
    """An orchestrator cannot be found or loaded safely."""


class DriverBusyError(RuntimeError):
    """Another driver (CLI process or native session) owns the orchestrator."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _check_record(record: OrchestratorRecord) -> None:
    checks = (
        (record.schema_version == SCHEMA_VERSION, "unsupported schema version"),
        (record.phase in PHASES, f"unknown phase {record.phase!r}"),
        (record.source in SOURCES, f"unknown source {record.source!r}"),
        (record.mode in MODES, f"unknown mode {record.mode!r}"),
    )
    for valid, message in checks:
        if not valid:
            raise ValueError(message)
    for task in record.tasks:
        if task.status not in TASK_STATUSES:
            raise ValueError(f"unknown task status {task.status!r}")


class OrchestratorStore:
    def __init__(self, repo: Path):
        self.repo = Path(repo).resolve()
        self.directory = self.repo / ".meow" / "orchestrators"

    def dir_for(self, orch_id: str) -> Path:
        if not is_orchestrator_id(orch_id):
            raise OrchestratorStateError(
                f"Unknown orchestrator ID {orch_id!r}. Inspect with "
                "meow orchestrate status."
            )
        return self.directory / orch_id

    def cancel_marker(self, orch_id: str) -> Path:
        return self.dir_for(orch_id) / "cancel"

    def create(  # ruff: ignore[too-many-arguments]
        self,
        *,
        objective: str,
        source: str,
        base_branch: str,
        mode: str,
        max_parallel: int,
        options: dict,
    ) -> OrchestratorRecord:
        now = _now()
        record = OrchestratorRecord(
            id=new_orchestrator_id(),
            objective=objective,
            source=source,
            base_branch=base_branch,
            mode=mode,
            created_at=now,
            updated_at=now,
            max_parallel=max_parallel,
            options=dict(options),
        )
        _check_record(record)
        return self.save(record)

    def save(self, record: OrchestratorRecord) -> OrchestratorRecord:
        """Write the record atomically and stamp `updated_at`."""
        directory = self.dir_for(record.id)
        directory.mkdir(parents=True, exist_ok=True)
        record.updated_at = _now()
        payload = json.dumps(asdict(record), indent=2, sort_keys=True) + "\n"
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=directory,
                prefix=f"{STATE_FILE}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, directory / STATE_FILE)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return record

    def load(self, orch_id: str) -> OrchestratorRecord:
        path = self.dir_for(orch_id) / STATE_FILE
        if not path.is_file():
            raise OrchestratorStateError(
                f"Orchestrator {orch_id} is missing. Inspect with "
                "meow orchestrate status."
            )
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            tasks = [OrchestratorTask(**item) for item in raw.pop("tasks")]
            record = OrchestratorRecord(**raw, tasks=tasks)
            _check_record(record)
            if record.id != orch_id:
                raise ValueError("record id does not match its directory")
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            raise OrchestratorStateError(
                f"Orchestrator {orch_id} is corrupt ({exc}). Preserve {path} "
                "and inspect it by hand."
            ) from exc
        return record

    def update(
        self, orch_id: str, mutate: Callable[[OrchestratorRecord], None]
    ) -> OrchestratorRecord:
        record = self.load(orch_id)
        mutate(record)
        return self.save(record)

    def records(self) -> list[OrchestratorRecord]:
        """Every loadable orchestrator, oldest first; corrupt ones are skipped."""
        if not self.directory.is_dir():
            return []
        loaded = []
        for path in self.directory.iterdir():
            if not path.is_dir() or not is_orchestrator_id(path.name):
                continue
            try:
                loaded.append(self.load(path.name))
            except OrchestratorStateError:
                continue
        return sorted(loaded, key=lambda record: record.created_at)

    def latest(self) -> OrchestratorRecord:
        records = self.records()
        if not records:
            raise OrchestratorStateError(
                "No orchestrators found. Start one with meow orchestrate."
            )
        return max(records, key=lambda record: record.updated_at)


class DriverLock:
    """One driver per orchestrator: a CLI process (PID) or a native session.

    A CLI lock names the owning PID and is reclaimable when that PID is dead.
    A native lock carries a heartbeat that every mutating helper refreshes, is
    reclaimable after `NATIVE_HEARTBEAT_SECONDS` without one. A native helper
    records its process identity while running, so the next native call may
    take over after it exits, while concurrent helpers cannot steal its lock.
    """

    def __init__(self, directory: Path, *, mode: Literal["cli", "native"]):
        self.path = Path(directory) / LOCK_FILE
        self.mode = mode
        self._owned = False

    def _payload(self) -> str:
        if self.mode == "native":
            data = {
                "mode": "native",
                "pid": os.getpid(),
                "start_identity": process_identity(os.getpid()),
                "heartbeat_at": time.time(),
                "started_at": _now(),
            }
        else:
            data = {
                "mode": "cli",
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "started_at": _now(),
            }
        return json.dumps(data)

    def holder(self) -> dict | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _unreadable_is_stale(self) -> bool:
        try:
            age = time.time() - self.path.stat().st_mtime
        except OSError:
            return False
        return age > _UNREADABLE_LOCK_GRACE_SECONDS

    def _reclaimable(self, holder: dict | None) -> bool:
        if holder is None:
            return self._unreadable_is_stale()
        if holder.get("mode") == "native":
            beat = holder.get("heartbeat_at")
            return not self._live_native(holder) and (
                not isinstance(beat, (int, float))
                or time.time() - beat > NATIVE_HEARTBEAT_SECONDS
            )
        pid = holder.get("pid")
        return not isinstance(pid, int) or not _pid_is_alive(pid)

    @staticmethod
    def _live_native(holder: dict) -> bool:
        pid = holder.get("pid")
        identity = holder.get("start_identity")
        return (
            isinstance(pid, int)
            and _pid_is_alive(pid)
            and (identity is None or process_identity(pid) == identity)
        )

    def _write_over(self) -> None:
        temporary = self.path.with_name(f"{LOCK_FILE}.{os.getpid()}.tmp")
        temporary.write_text(self._payload(), encoding="utf-8")
        os.replace(temporary, self.path)

    def _take_native(self, holder: dict) -> bool:
        if holder.get("pid") != os.getpid() and self._live_native(holder):
            return False
        self._write_over()
        self._owned = True
        return True

    def acquire(self) -> bool:
        from meow.ui.sessions import _file_transaction

        # Serializing the read and replacement prevents two helpers from both
        # taking over the same dead native holder. The kernel releases this
        # short transaction lock even when a claimant crashes.
        with _file_transaction(
            self.path.with_name("driver.claim.lock"), "orchestrator driver"
        ):
            return self._acquire()

    def _acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # A stale lock is reclaimed once; the bounded retry keeps a concurrent
        # driver from turning a missing lock into recursion.
        for _attempt in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                holder = self.holder()
                if self.mode == "native" and holder and holder.get("mode") == "native":
                    return self._take_native(holder)
                if self._reclaimable(holder):
                    self.path.unlink(missing_ok=True)
                    continue
                return False
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(self._payload())
            self._owned = True
            return True
        return False

    def heartbeat(self) -> None:
        """Refresh a native lock's heartbeat; a CLI lock needs none."""
        if self._owned and self.mode == "native":
            self._write_over()

    def release(self) -> None:
        if self._owned:
            self.path.unlink(missing_ok=True)
            self._owned = False

    def _describe(self) -> str:
        holder = self.holder() or {}
        if holder.get("mode") == "native":
            return "a native session"
        return f"CLI process {holder.get('pid', '?')} on {holder.get('host', '?')}"

    def __enter__(self) -> "DriverLock":
        if not self.acquire():
            raise DriverBusyError(
                f"Another driver already owns this orchestrator ({self._describe()}). "
                "Wait for it to finish, or stop it first."
            )
        return self

    def __exit__(self, *_exc) -> None:
        self.release()
