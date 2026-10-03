"""Automatic, read-only preparation for feature runs."""

from .core import (
    PreplanDecision,
    PreplanResult,
    ProjectContextEvidence,
    assess_preplan,
    gather_context,
    needs_breadboard,
    prepare_preplan,
)

__all__ = [
    "PreplanDecision",
    "PreplanResult",
    "ProjectContextEvidence",
    "assess_preplan",
    "gather_context",
    "needs_breadboard",
    "prepare_preplan",
]
