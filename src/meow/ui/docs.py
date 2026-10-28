"""Serve the project's existing Markdown for the UI Docs page."""

from pathlib import Path

from meow.installer.releases import RELEASE_DATA
from meow.project.custom import frontmatter

# Reader-facing guides in display order. ARCHITECTURE, RELEASING and ROADMAP
# stay maintainer-only.
GUIDES = (
    "README.md",
    "docs/INSTALL.md",
    "docs/ONBOARDING.md",
    "docs/CLI.md",
    "docs/RUNS.md",
    "docs/PIPELINE.md",
    "docs/WORKFLOWS.md",
    "docs/VERIFICATION.md",
    "docs/ORCHESTRATION.md",
    "docs/CUSTOM.md",
    "docs/HOOKS.md",
    "docs/INTEGRATIONS.md",
    "docs/CLEANUP.md",
    "docs/WORKTREE_SETUP.md",
    "docs/UI.md",
    "docs/SKILLS.md",
)


def docs_root() -> Path | None:
    """Wheel data directory, else the checkout root, else nothing to serve."""
    if (RELEASE_DATA / "docs").is_dir():
        return RELEASE_DATA
    # Checkout heuristic; a bare site-packages install has no docs/ here.
    checkout = Path(__file__).resolve().parents[3]
    if (checkout / "docs").is_dir():
        return checkout
    return None


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def _guides(root: Path) -> list[dict]:
    guides = []
    for rel in GUIDES:
        text = _read(root / rel)
        if text is not None:
            guides.append({"path": rel, "title": _title(text, rel), "text": text})
    return guides


def _skills(root: Path) -> list[dict]:
    plugin = root / "plugin" / "skills"
    base = plugin if plugin.is_dir() else root / "skills"
    skills = []
    for path in sorted(base.glob("*/SKILL.md")):
        try:
            meta, body = frontmatter.parse(path)
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        name = path.parent.name
        skills.append({
            "path": f"skills/{name}/SKILL.md",
            "title": f"/meow:{name}",
            "description": str(meta.get("description", "")),
            "text": body,
        })
    return skills


def build_docs() -> dict:
    root = docs_root()
    if root is None:
        return {"guides": [], "skills": []}
    return {"guides": _guides(root), "skills": _skills(root)}
