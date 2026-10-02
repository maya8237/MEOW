"""Optional request shaping and breadboard artifacts."""

# ruff: noqa
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class ShapeRecommendation:
    recommended: bool
    reasons: tuple[str, ...]
    open_questions: tuple[str, ...]


@dataclass(frozen=True)
class ShapeContext:
    artifact_path: str
    chosen_approach: str
    assumptions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ShapeOption:
    name: str
    description: str = ""


@dataclass(frozen=True)
class ShapeArtifact:
    problem: str
    constraints: tuple[str, ...]
    options: tuple[ShapeOption, ...]
    fit_checks: tuple[str, ...]
    chosen_approach: str
    assumptions: tuple[str, ...]


@dataclass(frozen=True)
class BreadboardArtifact:
    places: tuple[str, ...]
    affordances: tuple[str, ...]
    wiring: tuple[str, ...]
    vertical_slices: tuple[str, ...]
    reflection: tuple[str, ...] = ()


def assess_request(request: str) -> ShapeRecommendation:
    words = request.lower().split()
    broad = len(words) > 45 or any(
        token in request.lower()
        for token in (
            "dashboard",
            "platform",
            "across",
            "multiple components",
            "end to end",
        )
    )
    ambiguous = any(
        token in request.lower()
        for token in ("improve", "better", "support", "flexible", "somehow")
    )
    return ShapeRecommendation(
        broad or ambiguous,
        (
            "request spans multiple concerns"
            if broad
            else "request leaves an important choice open",
        )
        if broad or ambiguous
        else (),
        ("What is the preferred boundary or outcome?",) if ambiguous else (),
    )


def _atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def save_shape_artifact(
    path: Path, artifact: ShapeArtifact | BreadboardArtifact
) -> None:
    _atomic(
        path,
        {"version": 1, "kind": type(artifact).__name__, "artifact": asdict(artifact)},
    )


def load_shape_artifact(path: Path) -> ShapeArtifact | BreadboardArtifact:
    payload = json.loads(path.read_text(encoding="utf-8"))["artifact"]
    if "problem" in payload:
        payload["options"] = tuple(ShapeOption(**item) for item in payload["options"])
        for key in ("constraints", "fit_checks", "assumptions"):
            payload[key] = tuple(payload[key])
        return ShapeArtifact(**payload)
    for key in ("places", "affordances", "wiring", "vertical_slices", "reflection"):
        payload[key] = tuple(payload.get(key, ()))
    return BreadboardArtifact(**payload)


def reflect_breadboard(artifact: BreadboardArtifact) -> tuple[str, ...]:
    findings = []
    if not artifact.places:
        findings.append("missing place names")
    if not artifact.affordances:
        findings.append("missing affordances")
    if not artifact.wiring:
        findings.append("missing connections")
    if not artifact.vertical_slices:
        findings.append("missing vertical slices")
    if (
        artifact.vertical_slices
        and artifact.wiring
        and len(artifact.vertical_slices) > len(artifact.wiring)
    ):
        findings.append("inconsistent slices: more slices than wired connections")
    return tuple(findings)
