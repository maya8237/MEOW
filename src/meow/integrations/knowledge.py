"""Evidence-backed, report-only project knowledge checks."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

FindingKind = Literal["missing", "broken_link", "drift", "guidance"]


@dataclass(frozen=True)
class KnowledgeConfig:
    required_paths: tuple[str, ...] = ("AGENTS.md", "docs/ARCHITECTURE.md")
    docs_roots: tuple[str, ...] = ("docs", ".")


@dataclass(frozen=True)
class AuditFinding:
    kind: FindingKind
    path: str | None
    evidence: tuple[str, ...]
    severity: str
    advisory: bool
    suggested_action: str
    proposed_document: str | None = None

    @property
    def id(self) -> str:
        raw = json.dumps(asdict(self), sort_keys=True)
        return hashlib.sha1(raw.encode(), usedforsecurity=False).hexdigest()[:12]

    def to_dict(self) -> dict:
        value = asdict(self)
        value["evidence"] = list(self.evidence)
        value["id"] = self.id
        return value


@dataclass(frozen=True)
class KnowledgeAudit:
    root: Path
    findings: tuple[AuditFinding, ...]
    checked_paths: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "root": str(self.root),
            "findings": [finding.to_dict() for finding in self.findings],
            "checked_paths": list(self.checked_paths),
        }

    def write_report(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")


_LINK = re.compile(r"\[[^]]+\]\(([^)]+)\)")
_CODE_WORDS = re.compile(r"\b(?:class|def|function|module|package|src|api|CLI)\b", re.I)


def _evidence(rel: str, line: int, text: str) -> str:
    """Evidence cites the repository-relative path, so finding IDs (which hash
    it) are the same in every checkout and worktree."""
    return f"{rel}:{line}: {text.strip()}"


def audit_project(
    root: Path, *, config: KnowledgeConfig | None = None
) -> KnowledgeAudit:
    root = root.resolve()
    config = config or KnowledgeConfig()
    findings: list[AuditFinding] = []
    checked: set[str] = set()
    for required in config.required_paths:
        target = root / required
        checked.add(target.relative_to(root).as_posix())
        if not target.is_file():
            findings.append(
                AuditFinding(
                    "missing",
                    required,
                    (f"missing required path: {required}",),
                    "warning",
                    False,
                    f"Create {required} from observed repository evidence.",
                    required,
                )
            )
    docs: list[Path] = []
    for relative in config.docs_roots:
        base = root / relative
        if base.is_file() and base.suffix.lower() in {".md", ".markdown"}:
            docs.append(base)
        elif base.is_dir():
            docs.extend(p for p in base.rglob("*.md") if p.is_file())
    for path in sorted(set(docs)):
        rel = path.relative_to(root).as_posix()
        checked.add(rel)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(lines, 1):
            for target_text in _LINK.findall(line):
                target_text = target_text.split("#", 1)[0].strip()
                if (
                    not target_text
                    or "://" in target_text
                    or target_text.startswith("mailto:")
                ):
                    continue
                target = (path.parent / target_text).resolve()
                try:
                    target.relative_to(root)
                except ValueError:
                    findings.append(
                        AuditFinding(
                            "broken_link",
                            rel,
                            (_evidence(rel, number, line),),
                            "error",
                            False,
                            "Replace the link with an in-repository target or an intentional external URL.",
                            None,
                        )
                    )
                    continue
                if not target.exists():
                    findings.append(
                        AuditFinding(
                            "broken_link",
                            rel,
                            (_evidence(rel, number, line),),
                            "error",
                            False,
                            "Fix or remove the relative link after verifying the intended target.",
                            None,
                        )
                    )
        if _CODE_WORDS.search("\n".join(lines)) and any(
            word in " ".join(lines).lower() for word in ("always", "never", "must")
        ):
            findings.append(
                AuditFinding(
                    "drift",
                    rel,
                    (_evidence(rel, 1, lines[0] if lines else rel),),
                    "info",
                    True,
                    "Human-verify that prescriptive prose still matches the current implementation.",
                    None,
                )
            )
    findings.sort(
        key=lambda item: (
            (item.path or ""),
            item.evidence[0] if item.evidence else "",
            item.kind,
        )
    )
    return KnowledgeAudit(root, tuple(findings), tuple(sorted(checked)))


def structural_check(root: Path, audit: KnowledgeAudit | None = None) -> dict:
    audit = audit or audit_project(root)
    blocking = [f.id for f in audit.findings if not f.advisory]
    advisory = [f.id for f in audit.findings if f.advisory]
    return {
        "passed": not blocking,
        "finding_ids": blocking,
        "advisory_finding_ids": advisory,
    }


def select_findings(
    audit: KnowledgeAudit, ids: Sequence[str]
) -> tuple[AuditFinding, ...]:
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate finding selection")
    by_id = {finding.id: finding for finding in audit.findings}
    unknown = [item for item in ids if item not in by_id]
    if unknown:
        raise ValueError("unknown finding id(s): " + ", ".join(unknown))
    return tuple(by_id[item] for item in ids)
