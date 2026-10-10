"""Evidence-backed, advisory project quality concerns."""

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

STORE = Path(".meow/quality-concerns.json")
MAX_CONCERNS = 200
MAX_EVIDENCE = 500


class QualityStoreError(ValueError):
    """The concern store needs manual inspection before it can be updated."""


@dataclass(frozen=True)
class Concern:
    id: str
    path: str
    evidence: str
    impact: str
    follow_up: str
    first_run: str
    last_run: str
    state: str = "active"


def _path(repo: Path) -> Path:
    return Path(repo).resolve() / STORE


def load_concerns(repo: Path) -> list[Concern]:
    path = _path(repo)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list) or len(data) > MAX_CONCERNS:
            raise ValueError("invalid concern list")
        concerns = [Concern(**item) for item in data]
        if any(item.state not in {"active", "resolved", "stale"} for item in concerns):
            raise ValueError("invalid concern state")
        return concerns
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise QualityStoreError(
            f"Concern store is corrupt; preserve and inspect {path}"
        ) from exc


def _write(repo: Path, concerns: list[Concern]) -> None:
    path = _path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(
                [asdict(item) for item in concerns[-MAX_CONCERNS:]], stream, indent=2
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _evidence_exists(repo: Path, path: str, evidence: str) -> bool:
    if not path or not evidence or len(evidence) > MAX_EVIDENCE:
        return False
    root = Path(repo).resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return False
    try:
        return evidence in target.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False


def _refresh(repo: Path, concerns: list[Concern]) -> list[Concern]:
    return [
        Concern(**{**asdict(item), "state": "stale"})
        if item.state == "active"
        and not _evidence_exists(repo, item.path, item.evidence)
        else item
        for item in concerns
    ]


def record_concerns(  # ruff: ignore[too-many-statements]
    repo: Path,
    run_id: str,
    candidates: list[dict],
    *,
    evidence_root: Path | None = None,
) -> list[Concern]:
    """Retain only candidates grounded by exact text in a repository file."""
    evidence_root = evidence_root or repo
    concerns = _refresh(evidence_root, load_concerns(repo))
    by_id = {item.id: item for item in concerns}
    accepted = []
    for candidate in candidates:
        try:
            path = str(candidate["path"])
            evidence = str(candidate["evidence"])
            impact = str(candidate["impact"])
            follow_up = str(candidate["follow_up"])
        except (KeyError, TypeError):
            continue
        if (
            not impact.strip()
            or not follow_up.strip()
            or not _evidence_exists(evidence_root, path, evidence)
        ):
            continue
        identity = hashlib.sha256(f"{path}\0{evidence}".encode()).hexdigest()[:16]
        previous = by_id.get(identity)
        concern = Concern(
            identity,
            path,
            evidence,
            impact[:500],
            follow_up[:500],
            previous.first_run if previous else run_id,
            run_id,
            "active",
        )
        by_id[identity] = concern
        accepted.append(concern)
    updated = list(by_id.values())[-MAX_CONCERNS:]
    if updated != concerns:
        _write(repo, updated)
    return list({item.id: item for item in accepted}.values())


def concerns_for_run(
    repo: Path, run_id: str, *, evidence_root: Path | None = None
) -> list[Concern]:
    """Read current evidence for concerns last observed in this run."""
    return [
        item
        for item in _refresh(evidence_root or repo, load_concerns(repo))
        if item.state == "active" and item.last_run == run_id
    ]


def extract_concern_candidates(verdict: str) -> list[dict]:
    """Read explicit reviewer concern records; ignore unsupported prose."""
    candidates = []
    for line in verdict.splitlines():
        if not line.startswith("QUALITY_CONCERN: "):
            continue
        try:
            value = json.loads(line.removeprefix("QUALITY_CONCERN: "))
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append(value)
    return candidates
