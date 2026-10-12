"""Automatic conversion of a legacy `.harness.toml` project to `.meow/config.toml`.

Deterministic and conservative: it only creates the shared config when none
exists, never modifies or deletes the legacy file, and drops `docs_dir` so the
default `.meow/plans` applies. Moving plans and other judgement calls stay with
the `/meow:migration` skill.
"""

import re
from pathlib import Path

LEGACY_CONFIG_RELPATH = ".harness.toml"
CONFIG_RELPATH = ".meow/config.toml"

_TABLE = re.compile(r"^\s*\[")
_DOCS_DIR = re.compile(r"^\s*docs_dir\s*=")


def _without_docs_dir(text: str) -> str:
    kept: list[str] = []
    in_root = True
    for line in text.splitlines():
        if _TABLE.match(line):
            in_root = False
        if in_root and _DOCS_DIR.match(line):
            continue
        kept.append(line)
    return "\n".join(kept).rstrip() + "\n"


def migrate_legacy_config(root: Path) -> str | None:
    """Create `.meow/config.toml` from `.harness.toml`; the new path if written."""
    root = Path(root)
    legacy = root / LEGACY_CONFIG_RELPATH
    target = root / CONFIG_RELPATH
    if not legacy.is_file() or target.exists():
        return None
    text = legacy.read_text(encoding="utf-8")
    header = (
        "# Migrated automatically from .harness.toml (kept as a rollback reference).\n"
        "# Move private values to .meow/config.local.toml; see /meow:migration.\n\n"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(header + _without_docs_dir(text), encoding="utf-8")
    return CONFIG_RELPATH
