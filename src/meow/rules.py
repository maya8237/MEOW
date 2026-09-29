"""Project rules from docs/RULES.md, injected into role system prompts.

Mirrors docs/ARCHITECTURE.md's location convention (under `docs/`, not
`docs_dir`-relative) but not its discovery mechanism: ARCHITECTURE.md is
found opportunistically by an agent's own Read/Grep/Glob calls (see
reviewer.py's `_architecture_review_instructions`), which is too weak a
signal for rules that must be followed proactively rather than only
caught after the fact. RULES.md is read directly here and injected as
literal system-prompt text instead. Dependency-free of Sprint/AgentContext
/any agent module -- takes a plain Path and str, same as worktree.py's
"only ever touches the filesystem" pattern.
"""

import re
from pathlib import Path

RULES_FILENAME = "RULES.md"

_ROLE_NAMES = ("explorer", "planner", "generator", "reviewer")
_ROLE_HEADING = re.compile(
    r"^##\s*(" + "|".join(_ROLE_NAMES) + r")\s*#*\s*$", re.IGNORECASE
)


def _parse_rules_text(text: str) -> tuple[str, dict[str, str]]:
    """Split RULES.md content into (global_text, {role: role_text}).

    Only an exact, level-2 `## <role>` heading (case-insensitive, one of
    the four recognized role names) starts a new role section. Everything
    else -- including headings that merely contain a role word, or use a
    different level -- stays part of whichever section is currently
    active, defaulting to global at the start of the file. Two headings
    for the same role merge into one section.
    """
    global_lines: list[str] = []
    role_lines: dict[str, list[str]] = {}
    current_role: str | None = None

    for line in text.splitlines():
        match = _ROLE_HEADING.match(line.strip())
        if match:
            current_role = match.group(1).lower()
            role_lines.setdefault(current_role, [])
            continue
        target = global_lines if current_role is None else role_lines[current_role]
        target.append(line)

    global_text = "\n".join(global_lines).strip()
    role_sections = {
        role: "\n".join(lines).strip()
        for role, lines in role_lines.items()
        if "\n".join(lines).strip()
    }
    return global_text, role_sections


def load_rules(active_dir: Path, role: str) -> str | None:
    """Read docs/RULES.md under `active_dir` and return this role's combined
    global + role-specific rules text, ready to append to a system prompt.

    Returns None when the file doesn't exist, is empty, or has nothing
    that applies to `role` -- callers append unconditionally when this
    isn't None and otherwise leave their prompt untouched, so a missing
    file is fully backward compatible.
    """
    path = active_dir / "docs" / RULES_FILENAME
    if not path.exists():
        return None

    global_text, role_sections = _parse_rules_text(path.read_text(encoding="utf-8"))
    role_text = role_sections.get(role.lower(), "")
    parts = [part for part in (global_text, role_text) if part]
    if not parts:
        return None

    return "\n\nProject rules -- must follow:\n" + "\n\n".join(parts)
