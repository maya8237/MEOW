"""Atomic, deliberately small run journal shared by CLI and native flows."""

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

MAX_OUTPUT = 8000
SCHEMA_VERSION = 1
# Phases after which a run no longer drives work. The single source for worker
# reconciliation, cancellation, and plan-ownership checks.
TERMINAL_PHASES = frozenset({
    "complete",
    "delivered",
    "delivery_failed",
    "exhausted",
    "failed",
    "cancelled",
    "needs_user_decision",
})
_SECRET_KEY = re.compile(
    r"(?:token|secret|password|credential|api.?key|authorization)", re.I
)
_SECRET_VALUE = re.compile(
    r"(?i)\b(token|secret|password|api[_-]?key)\s*[:=]\s*[^\s,;]+"
)
_BEARER_VALUE = re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+\S+")
_ID = re.compile(r"[0-9a-f]{32}\Z")


class RunStateError(ValueError):
    """A run cannot be loaded safely; no agent should be started."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _safe(value: object, key: str = "") -> object:  # ruff: ignore[complex-structure, too-many-return-statements, too-many-branches]
    if (
        key
        in {
            "tokens",
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        }
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    ):
        return value
    if _SECRET_KEY.search(key):
        return "[redacted]"
    if value is None and key == "usage":
        return "unavailable"
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(k): _safe(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str):
        cleaned = _SECRET_VALUE.sub(lambda m: f"{m[1]}=[redacted]", value)
        cleaned = _BEARER_VALUE.sub("Authorization: Bearer [redacted]", cleaned)
        return cleaned[:MAX_OUTPUT] if key == "output" else cleaned
    if isinstance(value, (int, float, bool)):
        return value
    return str(value)


@dataclass(frozen=True)
class CheckResult:
    kind: str
    command: str
    required: bool
    exit_code: int | None
    duration: float
    output: str
    revision: str
    config_fingerprint: str
    timed_out: bool = False
    identity: str = ""

    @property
    def passed(self) -> bool:
        return not self.timed_out and self.exit_code == 0


@dataclass
class RunRecord:
    id: str
    source: str
    request: str
    repo: str
    worktree: str
    branch: str
    created_at: str
    updated_at: str
    schema_version: int = SCHEMA_VERSION
    sessions: dict[str, str] = field(default_factory=dict)
    phase: str = "created"
    attempt: int = 1
    round: int = 0
    plan_file: str | None = None
    plan_fingerprint: str | None = None
    config_fingerprint: str | None = None
    review_file: str | None = None
    last_failure: str | None = None
    results: dict = field(default_factory=dict)
    transitions: list[dict] = field(default_factory=list)
    usage: object = "unavailable"
    delivery: dict = field(default_factory=dict)
    background: dict = field(default_factory=dict)


class RunStore:
    def __init__(self, repo: Path):
        self.directory = Path(repo).resolve() / ".meow" / "runs"

    def _path(self, run_id: str) -> Path:
        if not _ID.fullmatch(run_id):
            raise RunStateError(
                "Unknown run ID. Inspect with meow status in "
                f"{self.directory.parent.parent}."
            )
        return self.directory / f"{run_id}.json"

    def _write(self, record: RunRecord) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(_safe(asdict(record)), indent=2, sort_keys=True) + "\n"
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.directory,
                prefix=f"{record.id}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path(record.id))
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def create(  # ruff: ignore[too-many-arguments]
        self,
        *,
        source: str,
        request: str,
        repo: Path,
        worktree: Path,
        branch: str,
    ) -> RunRecord:
        now = _now()
        record = RunRecord(
            uuid4().hex,
            source,
            request,
            str(Path(repo).resolve()),
            str(Path(worktree).resolve()),
            branch,
            now,
            now,
            transitions=[{"phase": "created", "at": now}],
        )
        self._write(record)
        return self.load(record.id)

    def load(  # ruff: ignore[complex-structure, too-many-branches, too-many-statements]
        self, run_id: str
    ) -> RunRecord:
        path = self._path(run_id)
        if not path.is_file():
            raise RunStateError(f"Run {run_id} is missing. Inspect with meow status.")
        try:
            raw = _safe(json.loads(path.read_text(encoding="utf-8")))
            if not isinstance(raw, dict):
                raise ValueError("record must be an object")
            schema_version = raw.get("schema_version", SCHEMA_VERSION)
            if schema_version != SCHEMA_VERSION:
                raise ValueError("unsupported schema version")
            raw.setdefault("schema_version", SCHEMA_VERSION)
            raw.setdefault("sessions", {})
            if not isinstance(raw["sessions"], dict) or any(
                not isinstance(role, str)
                or not role
                or not isinstance(session_id, str)
                or not session_id
                for role, session_id in raw["sessions"].items()
            ):
                raise ValueError("invalid sessions")
            record = RunRecord(**raw)
            fields = (record.phase, record.repo, record.worktree, record.branch)
            if record.id != run_id:
                raise ValueError("invalid record")
            if not isinstance(record.transitions, list):
                raise ValueError("invalid transitions")
            if not isinstance(record.results, dict):
                raise ValueError("invalid results")
            if not isinstance(record.schema_version, int) or isinstance(
                record.schema_version, bool
            ):
                raise ValueError("invalid schema version")
            if not isinstance(record.sessions, dict):
                raise ValueError("invalid sessions")
            if not all(isinstance(value, str) for value in fields):
                raise ValueError("invalid record")
            if (
                datetime.fromisoformat(record.created_at).tzinfo is None
                or datetime.fromisoformat(record.updated_at).tzinfo is None
            ):
                raise ValueError("timestamps require timezone")
            if not all(
                isinstance(item, dict)
                and isinstance(item.get("phase"), str)
                and isinstance(item.get("at"), str)
                for item in record.transitions
            ):
                raise ValueError("invalid transition history")
        except (OSError, ValueError, TypeError) as exc:
            raise RunStateError(
                f"Run {run_id} is corrupt. Preserve {path} and inspect with "
                "meow status."
            ) from exc
        return record

    def record_paths(self) -> list[Path]:
        """Run records only; sidecars such as `<id>.preplan.json` are skipped."""
        return [
            path for path in self.directory.glob("*.json") if _ID.fullmatch(path.stem)
        ]

    def latest(self) -> RunRecord:
        paths = sorted(self.record_paths(), key=lambda p: p.stat().st_mtime_ns)
        if not paths:
            raise RunStateError("No runs found. Start one with meow run.")
        return self.load(paths[-1].stem)

    def add_usage(self, run_id: str, entry: dict[str, object]) -> RunRecord:
        """Append a received SDK result without changing the workflow phase."""
        record = self.load(run_id)
        previous = record.usage if isinstance(record.usage, dict) else {}
        entries = previous.get("entries", [])
        if not isinstance(entries, list):
            entries = []
        record.usage = {"entries": [*entries, _safe(entry)]}
        record.updated_at = _now()
        self._write(record)
        return self.load(run_id)

    def set_session(self, run_id: str, role: str, session_id: str) -> RunRecord:
        """Retain only the Claude session reference needed for resume."""
        if not isinstance(role, str) or not role.strip():
            raise ValueError("session role must be a non-empty string")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session ID must be a non-empty string")
        record = self.load(run_id)
        role = role.strip().lower()
        if record.sessions.get(role) == session_id:
            return record
        record.sessions[role] = session_id
        record.updated_at = _now()
        self._write(record)
        return self.load(run_id)

    def update_background(self, run_id: str, **patch: object) -> RunRecord:
        """Update worker metadata without changing the workflow phase."""
        record = self.load(run_id)
        record.background.update({
            key: _safe(value, key) for key, value in patch.items()
        })
        record.updated_at = _now()
        self._write(record)
        return self.load(run_id)

    def transition(self, run_id: str, phase: str, **patch: object) -> RunRecord:
        record = self.load(run_id)
        if not phase or not isinstance(phase, str):
            raise ValueError("phase must be a non-empty string")
        unknown = set(patch) - set(RunRecord.__dataclass_fields__)
        if unknown or "id" in patch or "transitions" in patch:
            raise ValueError(f"Invalid run transition fields: {sorted(unknown)}")
        for key, value in patch.items():
            safe_value = _safe(value, key)
            if key == "results" and isinstance(safe_value, dict):
                record.results.update(safe_value)
            else:
                setattr(record, key, safe_value)
        record.phase = phase
        record.updated_at = _now()
        record.transitions.append({
            "phase": phase,
            "at": record.updated_at,
            "attempt": record.attempt,
            "round": record.round,
        })
        self._write(record)
        return self.load(run_id)
