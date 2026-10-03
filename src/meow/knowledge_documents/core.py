"""Explicit, safe creation of selected knowledge documents."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from meow.knowledge import AuditFinding


class DocumentWriter(Protocol):
    def write(self, root: Path, finding: AuditFinding) -> str: ...


@dataclass(frozen=True)
class DocumentCreationResult:
    created: tuple[str, ...]
    skipped: tuple[str, ...]
    warnings: tuple[str, ...]


def _output(finding: AuditFinding) -> str | None:
    return finding.proposed_document


def create_selected_documents(
    root: Path,
    findings: Sequence[AuditFinding],
    *,
    writer: DocumentWriter,
    overwrite: bool = False,
) -> DocumentCreationResult:
    root = root.resolve()
    created, skipped, warnings = [], [], []
    seen: set[str] = set()
    for finding in findings:
        relative = _output(finding)
        if not relative or relative in seen:
            continue
        seen.add(relative)
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                f"proposed document escapes repository: {relative}"
            ) from exc
        if target.exists() and not overwrite:
            skipped.append(relative)
            continue
        content = writer.write(root, finding)
        if not content.strip():
            warnings.append(f"empty document skipped: {relative}")
            continue
        if "uncertainty" not in content.lower():
            content += "\n\n## Uncertainty\n\n- Human verification is required where repository evidence is incomplete.\n"
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        created.append(relative)
    return DocumentCreationResult(tuple(created), tuple(skipped), tuple(warnings))


class EvidenceDocumentWriter:
    def write(self, root: Path, finding: AuditFinding) -> str:
        observations: list[str] = []
        max_observations = 12
        extensions = {".py", ".js", ".ts", ".tsx", ".go", ".rs", ".java"}
        for path in sorted(root.rglob("*")):
            if len(observations) >= max_observations:
                break
            if not path.is_file() or path.suffix.lower() not in extensions:
                continue
            relative = path.relative_to(root)
            if any(
                part.startswith(".") or part in {"node_modules", "venv", "__pycache__"}
                for part in relative.parts
            ):
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()[:80]
            except (OSError, UnicodeError):
                continue
            for number, line in enumerate(lines, 1):
                if line.strip().startswith((
                    "def ",
                    "class ",
                    "function ",
                    "export ",
                    "pub fn ",
                )):
                    observations.append(
                        f"- {relative.as_posix()}:{number}: {line.strip()[:120]}"
                    )
                    break
        if not observations:
            return ""
        evidence = "\n".join(observations)
        title = finding.proposed_document or "Project knowledge"
        return (
            f"# {title}\n\nObserved source entry points:\n{evidence}\n\n"
            "## Uncertainty\n\n- Module responsibilities and project policy require human verification.\n"
        )
