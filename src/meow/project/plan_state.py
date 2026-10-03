"""Atomic lifecycle metadata kept beside a canonical plan."""

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

STATES = ("draft", "in-progress", "complete")


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class PlanMetadata:
    plan: str
    run_id: str | None = None
    lifecycle: str = "draft"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    discoveries: list[str] = field(default_factory=list)


class PlanStore:
    def __init__(self, repo: Path):
        self.repo = Path(repo).resolve()

    @staticmethod
    def _path(plan: Path) -> Path:
        return Path(plan).with_name(Path(plan).name + ".state.json")

    def read(self, plan: Path) -> PlanMetadata:
        plan = Path(plan)
        path = self._path(plan)
        if not path.exists():
            return PlanMetadata(str(plan.resolve()))
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            value = PlanMetadata(**raw)
            if value.lifecycle not in STATES:
                raise ValueError("invalid plan lifecycle")
            return value
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            # A host can be stopped between the temp-file write and replace.
            # Treat an incomplete sidecar as absent so status and resume can
            # still recover the canonical plan.
            return PlanMetadata(str(plan.resolve()))

    def transition(  # ruff: ignore[too-many-statements]
        self, plan: Path, state: str, run_id: str | None = None, note: str | None = None
    ) -> PlanMetadata:
        if state not in STATES:
            raise ValueError(f"invalid plan lifecycle: {state}")
        current = self.read(plan)
        if STATES.index(state) < STATES.index(current.lifecycle):
            raise ValueError("plan lifecycle cannot move backwards")
        current.lifecycle, current.run_id, current.updated_at = (
            state,
            run_id or current.run_id,
            _now(),
        )
        if note:
            current.discoveries = [*current.discoveries, note][-20:]
        path = self._path(Path(plan))
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, delete=False
            ) as stream:
                temp = Path(stream.name)
                json.dump(asdict(current), stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            if temp:
                temp.unlink(missing_ok=True)
        return current


def discover_plan(  # ruff: ignore[complex-structure]
    docs_dir: Path, explicit: Path | None = None, run_id: str | None = None
) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_absolute():
            path = Path(docs_dir) / path
        if not path.is_file():
            raise FileNotFoundError(f"Plan file not found: {path}")
        return path.resolve()
    docs_dir = Path(docs_dir)
    candidates = [
        p
        for p in docs_dir.glob("*.md")
        if not p.name.endswith(("review.md", "-test.md"))
    ]
    if not candidates:
        raise FileNotFoundError(f"No plan file found in {docs_dir}")
    if run_id:
        linked = [p for p in candidates if PlanStore(docs_dir).read(p).run_id == run_id]
        if linked:
            return sorted(linked, key=lambda p: (p.stat().st_mtime_ns, p.name))[-1]
    return sorted(candidates, key=lambda p: (p.stat().st_mtime_ns, p.name))[-1]
