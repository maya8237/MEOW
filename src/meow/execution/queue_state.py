"""Durable queue records and repository-scoped worker locking."""

import json
import os
import socket
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


class QueueStateError(ValueError):
    """Queue state is missing, corrupt, or unsafe to use."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class QueueItem:
    id: str
    request: str
    feature_name: str | None
    status: str
    created_at: str
    updated_at: str
    attempts: int = 0
    run_id: str | None = None
    error: str | None = None


class QueueStore:
    def __init__(self, repo: Path):
        self.directory = Path(repo).resolve() / ".meow" / "queue"
        self.path = self.directory / "queue.json"

    def _load_all(self) -> list[QueueItem]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError
            return [QueueItem(**item) for item in raw]
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise QueueStateError(f"Queue state is corrupt: {self.path}") from exc

    def _save(self, items: list[QueueItem]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"{self.path.name}.{uuid4().hex}.tmp")
        payload = json.dumps([asdict(item) for item in items], indent=2)
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, self.path)

    def enqueue(self, request: str, feature_name: str | None = None) -> QueueItem:
        if not request.strip():
            raise ValueError("queue request must not be empty")
        now = _now()
        item = QueueItem(uuid4().hex, request, feature_name, "pending", now, now)
        items = self._load_all()
        items.append(item)
        self._save(items)
        return item

    def list(self) -> list[QueueItem]:
        return self._load_all()

    def pending(self) -> list[QueueItem]:
        return [item for item in self._load_all() if item.status == "pending"]

    def update(self, item_id: str, **changes: object) -> QueueItem:
        items = self._load_all()
        for item in items:
            if item.id == item_id:
                for key, value in changes.items():
                    if key not in QueueItem.__dataclass_fields__ or key in {
                        "id",
                        "created_at",
                    }:
                        raise ValueError(f"invalid queue field: {key}")
                    setattr(item, key, value)
                item.updated_at = _now()
                self._save(items)
                return item
        raise QueueStateError(f"Queue item {item_id} is missing")

    def claim(self, item_id: str) -> QueueItem:
        item = next(
            (candidate for candidate in self._load_all() if candidate.id == item_id),
            None,
        )
        if item is None or item.status != "pending":
            raise QueueStateError(f"Queue item {item_id} is not pending")
        return self.update(item_id, status="running", attempts=item.attempts + 1)


class QueueWorkerLock:
    def __init__(self, directory: Path):
        self.path = Path(directory) / "worker.lock"
        self._owned = False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"pid": os.getpid(), "host": socket.gethostname(), "started_at": _now()}
        )
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                pid = int(payload["pid"])
                os.kill(pid, 0)
            except ProcessLookupError:
                self.path.unlink(missing_ok=True)
                return self.acquire()
            except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
                return False
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
        self._owned = True
        return True

    def release(self) -> None:
        if self._owned:
            self.path.unlink(missing_ok=True)
            self._owned = False

    def __enter__(self):
        if not self.acquire():
            raise QueueStateError("queue worker already running")
        return self

    def __exit__(self, *_exc):
        self.release()
