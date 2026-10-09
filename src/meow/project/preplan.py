"""Read-only project context and bounded design choices before planning."""

import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from meow.project.shaping import (
    BreadboardArtifact,
    ShapeArtifact,
    ShapeOption,
    assess_request,
    reflect_breadboard,
)

MAX_TRACKED_FILES = 500
MAX_REFERENCES = 12
MAX_SNIPPET = 180
MAX_SNIPPETS_PER_FILE = 2
_WORD = re.compile(r"[a-z][a-z0-9_]{2,}", re.I)
_LINK = re.compile(r"\[[^]]+\]\(([^)]+)\)")
_CHOICE = re.compile(r"\b(?:choose between|either .+ or |whether .+ or )\b", re.I)
_UI = re.compile(r"\b(?:ui|dashboard|screen|page|form|browser|frontend)\b", re.I)
_CROSS = re.compile(
    r"\b(?:across|api|backend|database|multiple components|end to end)\b", re.I
)
_STOP = {
    "add",
    "fix",
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "into",
    "make",
    "work",
}


@dataclass(frozen=True)
class ProjectContextEvidence:
    references: tuple[str, ...]
    constraints: tuple[str, ...]
    uncertainty: tuple[str, ...]


@dataclass(frozen=True)
class PreplanDecision:
    mode: Literal["direct", "shape", "needs_user_decision"]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class PreplanResult:
    evidence: ProjectContextEvidence
    decision: PreplanDecision
    shape: ShapeArtifact | None = None
    breadboard: BreadboardArtifact | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _tracked_files(repo: Path) -> tuple[Path, ...]:
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z"],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        return ()
    names = result.stdout.decode("utf-8", errors="replace").split("\0")
    return tuple(repo / name for name in names[:MAX_TRACKED_FILES] if name)


def _snippets(path: Path, repo: Path, tokens: set[str]) -> list[tuple[int, str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[:300]
    except (OSError, UnicodeError):
        return []
    name = path.relative_to(repo).as_posix()
    selected = []
    for number, line in enumerate(lines, 1):
        if any(token in line.lower() for token in tokens) or any(
            token in name.lower() for token in tokens
        ):
            selected.append((number, line.strip()[:MAX_SNIPPET]))
            if len(selected) >= MAX_SNIPPETS_PER_FILE:
                break
    return selected


def _broken_guidance_links(path: Path, repo: Path) -> tuple[str, ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[:300]
    except (OSError, UnicodeError):
        return ()
    broken = []
    for number, line in enumerate(lines, 1):
        for raw in _LINK.findall(line):
            target = raw.split("#", 1)[0].strip()
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            resolved = (path.parent / target).resolve()
            if not resolved.is_relative_to(repo) or not resolved.exists():
                broken.append(
                    f"{path.relative_to(repo).as_posix()}:{number}: "
                    f"unresolved link {target}"
                )
    return tuple(broken[:MAX_REFERENCES])


def gather_context(  # ruff: ignore[complex-structure, too-many-branches, too-many-statements]
    repo: Path, request: str
) -> ProjectContextEvidence:
    """Gather bounded, cited code and guidance; never propose doc work."""
    repo = repo.resolve()
    tokens = set(_WORD.findall(request.lower())) - _STOP
    references: list[str] = []
    constraints: list[str] = []
    uncertainty: list[str] = []
    for path in _tracked_files(repo):
        if not path.is_file() or path.suffix.lower() not in {
            ".py",
            ".ts",
            ".tsx",
            ".js",
            ".go",
            ".rs",
            ".md",
            ".toml",
        }:
            continue
        rel = path.relative_to(repo).as_posix()
        if rel.startswith(".meow/"):
            continue
        if rel == "AGENTS.md":
            uncertainty.extend(_broken_guidance_links(path, repo))
        relevant = rel == "AGENTS.md" or bool(
            tokens.intersection(_WORD.findall(rel.lower()))
        )
        if not relevant:
            snippets = _snippets(path, repo, tokens)
        else:
            snippets = _snippets(path, repo, tokens) or _snippets(
                path, repo, {"#", "must", "use"}
            )
        for line, content in snippets:
            item = f"{rel}:{line}: {content}"
            if rel == "AGENTS.md":
                constraints.append(item)
            else:
                references.append(item)
            if len(references) + len(constraints) >= MAX_REFERENCES:
                break
        if len(references) + len(constraints) >= MAX_REFERENCES:
            break
    if not (repo / "AGENTS.md").is_file():
        uncertainty.append(
            "AGENTS.md guidance is unavailable; infer constraints from code and tests"
        )
    if not references:
        uncertainty.append("No request-specific code or test reference found")
    return ProjectContextEvidence(
        tuple(references), tuple(constraints), tuple(uncertainty)
    )


def assess_preplan(request: str, evidence: ProjectContextEvidence) -> PreplanDecision:
    if _CHOICE.search(request):
        return PreplanDecision(
            "needs_user_decision", ("Request presents incompatible product outcomes",)
        )
    assessment = assess_request(request)
    if assessment.recommended:
        return PreplanDecision("shape", assessment.reasons)
    return PreplanDecision("direct", ("Request is specific enough for a direct plan",))


def needs_breadboard(request: str, evidence: ProjectContextEvidence) -> bool:
    return bool(_UI.search(request) and _CROSS.search(request))


def prepare_preplan(
    repo: Path,
    request: str,
    *,
    unattended: bool = False,
    evidence: ProjectContextEvidence | None = None,
) -> PreplanResult:
    """Choose the shortest safe preparation path without asking questions."""
    evidence = evidence or gather_context(repo, request)
    decision = assess_preplan(request, evidence)
    if decision.mode == "needs_user_decision":
        return PreplanResult(evidence, decision)
    if decision.mode == "direct":
        return PreplanResult(evidence, decision)
    options = (
        ShapeOption(
            "minimal compatible change",
            "Preserve existing interfaces and solve the stated problem",
        ),
        ShapeOption(
            "broader redesign",
            "Change component boundaries only if evidence requires it",
        ),
    )
    shape = ShapeArtifact(
        problem=request,
        constraints=evidence.constraints,
        options=options,
        fit_checks=("Preserves existing behavior", "Has a verifiable outcome"),
        chosen_approach=options[0].name,
        assumptions=("Use existing project patterns where evidence is available",),
    )
    if not needs_breadboard(request, evidence):
        return PreplanResult(evidence, decision, shape)
    breadboard = BreadboardArtifact(
        places=("user interface", "backing service"),
        affordances=(request, "service returns outcome"),
        wiring=("user interface -> backing service",),
        vertical_slices=("verify user action with observable response",),
    )
    findings = reflect_breadboard(breadboard)
    breadboard = BreadboardArtifact(
        breadboard.places,
        breadboard.affordances,
        breadboard.wiring,
        breadboard.vertical_slices,
        findings,
    )
    return PreplanResult(evidence, decision, shape, breadboard)
