"""Tools for kit authors: scaffold a kit and check one before publishing."""

from dataclasses import dataclass, field
from pathlib import Path

from meow.project.custom.definitions import _load_skill, discover, load_agent, load_hook
from meow.project.custom.kit_store import default_name
from meow.project.custom.kits import MANIFEST, NAME, contents, validate_kit
from meow.project.custom.rendering import validate_agents
from meow.project.custom.sources import KINDS, CustomSource, builtin_sources
from meow.project.custom.workflows import load_workflow
from meow.project.custom.workflows import validate as validate_workflow

_IGNORED_ROOT = frozenset({
    "README.md",
    "LICENSE",
    "LICENSE.md",
    MANIFEST,
    ".gitignore",
})
MAX_BYTES = 10 * 1024 * 1024


@dataclass
class Report:
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _source(kind: str, root: Path) -> CustomSource:
    # Looks like a user kit; project scope would demand a git checkout.
    return CustomSource(
        kind, root / kind, "user", None, kit="validate", project_dir=root
    )


def _try(report: Report, load, path: Path, source: CustomSource) -> None:
    try:
        load(path, source)
    except (ValueError, OSError) as exc:
        report.problems.append(str(exc).replace(f"{path.parent.parent}/", ""))


def _files(root: Path, kind: str) -> list[Path]:
    folder = root / kind
    if not folder.is_dir():
        return []
    if kind == "skills":
        return sorted(p for p in folder.iterdir() if p.is_dir())
    suffixes = {".md", ".toml"} if kind == "agents" else {".toml"}
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix in suffixes)


def _check_files(report: Report, root: Path) -> None:
    loaders = {
        "agents": load_agent,
        "workflows": load_workflow,
        "hooks": load_hook,
    }
    for kind in KINDS:
        source = _source(kind, root)
        for path in _files(root, kind):
            if kind == "skills":
                if (path / "SKILL.md").is_file():
                    _try(report, lambda p, s: _load_skill(p, s), path, source)
                else:
                    report.warnings.append(f"skills/{path.name}: no SKILL.md; ignored")
            else:
                _try(report, loaders[kind], path, source)


def _check_whole(report: Report, root: Path) -> None:
    """Cross-file rules (duplicate names, delegates, template variables)."""
    sources = [_source(kind, root) for kind in KINDS if (root / kind).is_dir()]
    try:
        custom = discover([*sources, *builtin_sources()])
        validate_agents(custom)
    except ValueError as exc:
        report.problems.append(str(exc))
        return
    for workflow in custom.workflows:
        if workflow.source.kit == "validate":
            report.problems += validate_workflow(workflow, custom)
    for hook in custom.hooks:
        if hook.source.kit == "validate":
            report.summary.append(
                f"hook {hook.name} ({hook.event}) will run: {hook.command}"
            )


def _tree_bytes(report: Report, root: Path) -> int:
    total = 0
    for path in sorted(root.rglob("*")):
        if ".git" in path.relative_to(root).parts:
            continue
        if path.is_symlink() or path.is_junction():
            report.problems.append(f"{path.relative_to(root)}: symbolic link")
        elif path.is_file():
            total += path.stat().st_size
    return total


def _check_tree(report: Report, root: Path) -> None:
    total = _tree_bytes(report, root)
    if total > MAX_BYTES:
        report.warnings.append(f"kit is {total // 1024 // 1024} MB; keep kits small")
    known = {*KINDS, *_IGNORED_ROOT}
    report.warnings += [
        f"{entry.name}: not a kit folder; ignored"
        for entry in sorted(root.iterdir())
        if entry.name not in known and not entry.name.startswith(".")
    ]


def validate_directory(root: Path) -> Report:
    """Everything `meow kits add` would reject, plus advice, in one pass."""
    root = Path(root)
    report = Report()
    if not root.is_dir():
        report.problems.append(f"{root}: not a directory")
        return report
    try:
        validate_kit(root)
    except ValueError as exc:
        report.problems.append(str(exc))
    _check_tree(report, root)
    _check_files(report, root)
    if report.ok:
        _check_whole(report, root)
    counts = ", ".join(f"{len(n)} {kind}" for kind, n in contents(root).items())
    report.summary.insert(0, counts or "nothing found")
    return report


_MANIFEST = """\
# Optional. Delete anything you do not need.
name = "{name}"
description = "What this kit adds."

# Give a skill only to some MEOW roles (omit to give it to every role):
# [skills.roles]
# hello = ["reviewer"]
"""

_SKILL = """\
---
name: hello
description: Greets the user and states which kit this skill came from.
---
Say hello, then mention that this skill comes from the {name} kit.
"""

_AGENT = """\
description = "Read-only helper that explains how a module works."
tools = ["Read", "Grep", "Glob"]
prompt = '''
Explain how the code the user names works. Read it first and cite file paths.
Do not modify anything.
'''
"""

_README = """\
# {name}

A MEOW kit: skills, agents, workflows, and hooks, installed from git.

Check it, publish it, then install it from any machine:

    meow kits validate .
    git init && git add -A && git commit -m "Initial kit"
    # push to GitHub or GitLab, then:
    meow kits add owner/{name}

Folders (all optional): `skills/`, `agents/`, `workflows/`, `hooks/`.
See the MEOW kits guide for details.
"""


def scaffold(directory: Path, name: str | None = None) -> list[str]:
    """Create a small valid kit in a new or empty `directory`."""
    directory = Path(directory)
    name = name or default_name(directory.resolve().name)
    if not NAME.match(name):
        raise ValueError(f"invalid kit name {name!r}; use lowercase letters/digits/-")
    if directory.exists() and any(directory.iterdir()):
        raise ValueError(f"{directory} is not empty")
    files = {
        MANIFEST: _MANIFEST.format(name=name),
        "README.md": _README.format(name=name),
        "skills/hello/SKILL.md": _SKILL.format(name=name),
        "agents/explainer.toml": _AGENT,
    }
    for relative, text in files.items():
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return sorted(files)
