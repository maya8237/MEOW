"""Explicit, safe creation of selected knowledge documents."""
from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .knowledge import AuditFinding


class DocumentWriter(Protocol):
    def write(self, root: Path, finding: AuditFinding) -> str: ...


@dataclass(frozen=True)
class DocumentCreationResult:
    created: tuple[str, ...]
    skipped: tuple[str, ...]
    warnings: tuple[str, ...]


def _output(finding: AuditFinding) -> str | None:
    return finding.proposed_document


def create_selected_documents(root: Path, findings: Sequence[AuditFinding], *, writer: DocumentWriter, overwrite: bool = False) -> DocumentCreationResult:
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
            raise ValueError(f"proposed document escapes repository: {relative}") from exc
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
        evidence = "\n".join(f"- {item}" for item in finding.evidence)
        return f"# {finding.proposed_document or 'Project knowledge'}\n\nObserved evidence:\n{evidence}\n\n## Uncertainty\n\n- This document records observations only; verify policy with the project owner.\n"
