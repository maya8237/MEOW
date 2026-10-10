"""Atomic lifecycle metadata kept beside a canonical plan."""

import contextlib
import json
import os
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

_STALE_LOCK_SECONDS = 30
STATES = ("draft", "in-progress", "complete")


def _now() -> str:
    return datetime.now(UTC).isoformat()


class PlanOwnedError(RuntimeError):
    """A plan is still owned by another run that has not verifiably finished."""


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

    def claim(
        self,
        plan: Path,
        run_id: str,
        owner_finished: Callable[[str], bool],
    ) -> PlanMetadata:
        """Start a draft for ``run_id``, taking over only from a finished owner.

        ``owner_finished`` is asked about the previous owner run. It must return
        False when that run is active or its state cannot be verified.
        """
        with self._locked(Path(plan)):
            owner = self.read(plan).run_id
            takeover = owner is not None and owner != run_id
            if takeover and not owner_finished(owner):
                raise self._owned_error(plan, owner)
            return self.transition(plan, "draft", run_id, takeover=takeover)

    def assert_available(
        self, plan: Path, owner_finished: Callable[[str], bool]
    ) -> None:
        """Raise ``PlanOwnedError`` when another unfinished run owns ``plan``."""
        owner = self.read(plan).run_id
        if owner is not None and not owner_finished(owner):
            raise self._owned_error(plan, owner)

    @staticmethod
    def _owned_error(plan: Path, owner: str) -> PlanOwnedError:
        return PlanOwnedError(
            f"Plan {plan} is owned by run {owner}, which is still active or "
            "could not be verified. Wait for it to finish, inspect it with "
            f"`meow status {owner}`, or stop it with `meow cancel {owner}` "
            "before starting a new run for this plan."
        )

    @staticmethod
    @contextlib.contextmanager
    def _locked(plan: Path) -> Iterator[None]:
        """Exclusive claim section: `O_EXCL` lock file beside the sidecar.

        Two runs claiming one plan at the same moment would otherwise both
        read "no owner" and both win. A lock older than 30s is a crashed
        holder's and is broken.
        """
        lock = plan.with_name(plan.name + ".state.lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                break
            except FileExistsError:
                try:
                    if time.time() - lock.stat().st_mtime > _STALE_LOCK_SECONDS:
                        lock.unlink(missing_ok=True)
                        continue
                except OSError:
                    continue
                time.sleep(0.05)
        try:
            yield
        finally:
            lock.unlink(missing_ok=True)

    def transition(  # ruff: ignore[too-many-arguments, too-many-statements]
        self,
        plan: Path,
        state: str,
        run_id: str | None = None,
        note: str | None = None,
        *,
        takeover: bool = False,
    ) -> PlanMetadata:
        """Advance a plan's lifecycle. ``takeover`` restarts it for a new run.

        Callers pass ``takeover=True`` only after confirming the previous owner
        run has finished; otherwise a stale sidecar would block every restart.
        """
        if state not in STATES:
            raise ValueError(f"invalid plan lifecycle: {state}")
        current = self.read(plan)
        if takeover:
            current = PlanMetadata(str(Path(plan).resolve()))
        elif STATES.index(state) < STATES.index(current.lifecycle):
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
