"""Publish custom skills where Claude Code loads them natively, live.

Claude Code watches `.claude/skills/<name>/SKILL.md` (project and local) and
`~/.claude/skills/<name>/SKILL.md` (user): a new skill works at once, with no
restart (`/reload-plugins` also rescans). It ignores a plugin folder (manifest plus
`skills/`) placed there. Skills authored in a scope's conventional folder or a
`[custom].skills` directory are therefore copied to `<name>/` beside the rest,
marked with `.meow-managed` so a sync only ever replaces or removes its own
copies, never a skill the user wrote. Local copies are listed in
`.git/info/exclude`; project copies are meant to be committed.
"""

import shutil
import subprocess
from pathlib import Path

from meow.common.atomic import move_directory
from meow.project.custom.definitions import CustomSkill
from meow.project.custom.sources import scope_plugin_dir, source_files

FLAT_SCOPES = ("project", "local", "user")
MARKER = ".meow-managed"
_IGNORED = frozenset({MARKER, ".DS_Store", "Thumbs.db"})
_EXCLUDE_BEGIN = "# >>> MEOW local skills (generated)"
_EXCLUDE_END = "# <<< MEOW local skills"

Wanted = dict[str, tuple[Path, list[Path]]]  # name -> (source dir, its files)


def skills_root(scope: str, project_dir: Path) -> Path:
    base = Path.home() if scope == "user" else Path(project_dir)
    return base / ".claude" / "skills"


def _owner(folder: Path) -> str | None:
    """The scope that generated `folder`, or None when it is not MEOW's."""
    try:
        return (folder / MARKER).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _tree(directory: Path, files: list[Path] | None = None) -> dict[str, bytes]:
    found = files if files is not None else sorted(directory.rglob("*"))
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in found
        if path.is_file() and path.name not in _IGNORED
    }


def _current(source: Path, files: list[Path], target: Path) -> bool:
    return target.is_dir() and _tree(source, files) == _tree(target)


def _publish(scope: str, source: Path, files: list[Path], target: Path) -> None:
    staging = target.with_name(f".{target.name}.meow-new")
    shutil.rmtree(staging, ignore_errors=True)
    for path in files:
        if not path.is_file():
            continue
        copy = staging / path.relative_to(source)
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, copy)
    staging.mkdir(parents=True, exist_ok=True)
    (staging / MARKER).write_text(scope + "\n", encoding="utf-8")
    if target.exists():
        shutil.rmtree(target)
    move_directory(staging, target)


def native_wanted(scope: str, project_dir: Path, user_dir: Path) -> Wanted:
    """Skills in the scope's own folder, found without loading configuration."""
    folder = scope_plugin_dir(scope, project_dir, user_dir) / "skills"
    if not folder.is_dir():
        return {}
    return {
        path.name: (path, sorted(f for f in path.rglob("*") if f.is_file()))
        for path in sorted(folder.iterdir())
        if (path / "SKILL.md").is_file() and not path.name.startswith(".")
    }


def configured_wanted(
    scope: str, project_dir: Path, skills: tuple[CustomSkill, ...]
) -> Wanted:
    """Winning skills of `scope`, from its own folder and `[custom].skills`."""
    root = skills_root(scope, project_dir)
    return {
        skill.name: (skill.directory, source_files(skill.directory, skill.source))
        for skill in skills
        if skill.source.scope == scope
        and skill.directory.resolve() != (root / skill.name).resolve()
    }


def _wanted_problems(scope: str, root: Path, wanted: Wanted, *, fix: bool) -> list[str]:
    found = []
    for name, (source, files) in sorted(wanted.items()):
        target = root / name
        label = f"{scope} skill '{name}' ({target})"
        if target.exists() and _owner(target) != scope:
            found.append(f"{label}: not MEOW's copy; left unchanged, rename one")
        elif not _current(source, files, target):
            found.append(f"{label}: not published for Claude Code")
            if fix:
                _publish(scope, source, files, target)
    return found


def _stale_problems(scope: str, root: Path, wanted: Wanted, *, fix: bool) -> list[str]:
    found = []
    folders = sorted(root.iterdir()) if root.is_dir() else []
    for folder in folders:
        if folder.name in wanted or not folder.is_dir() or _owner(folder) != scope:
            continue
        found.append(f"{scope} skill '{folder.name}' ({folder}): source removed")
        if fix:
            shutil.rmtree(folder)
    return found


def flat_problems(
    scope: str, project_dir: Path, wanted: Wanted, *, fix: bool
) -> list[str]:
    """Describe (and with `fix`, repair) the scope's published skill copies."""
    root = skills_root(scope, project_dir)
    found = _wanted_problems(scope, root, wanted, fix=fix)
    found += _stale_problems(scope, root, wanted, fix=fix)
    if fix and scope == "local":
        _write_exclude(project_dir, sorted(wanted))
    return found


def _exclude_file(project_dir: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-path", "info/exclude"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode or not result.stdout.strip():
        return None
    path = Path(result.stdout.strip())
    return path if path.is_absolute() else Path(project_dir) / path


def _write_exclude(project_dir: Path, names: list[str]) -> None:
    """Keep local copies out of git without touching the shared `.gitignore`."""
    path = _exclude_file(project_dir)
    if path is None:
        return
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    before, _, rest = text.partition(_EXCLUDE_BEGIN)
    after = rest.partition(_EXCLUDE_END)[2] if rest else ""
    block = ""
    if names:
        rules = [f"/.claude/skills/{name}/" for name in names]
        block = "\n".join([_EXCLUDE_BEGIN, *rules, _EXCLUDE_END]) + "\n"
    new = before.rstrip("\n") + ("\n" if before.strip() else "") + block
    new += after.lstrip("\n")
    if new != text:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new, encoding="utf-8")


def local_skill_paths(project_dir: Path) -> list[str]:
    """Repo-relative folders of local skills MEOW published (never shared)."""
    root = skills_root("local", project_dir)
    folders = sorted(root.iterdir()) if root.is_dir() else []
    return [
        f".claude/skills/{folder.name}"
        for folder in folders
        if folder.is_dir() and _owner(folder) == "local"
    ]


def publish_native(scope: str, project_dir: Path, user_dir: Path) -> list[str]:
    """Publish the scope's own-folder skills; for callers without a loaded config."""
    wanted = native_wanted(scope, project_dir, user_dir)
    return flat_problems(scope, project_dir, wanted, fix=True)
