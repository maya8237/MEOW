"""Move customizations out of the marketplace layout earlier versions used.

Earlier versions served project and local plugins as directory marketplaces
(`.meow/plugin`, `.meow/local/plugin`, and `linked/plugin` beside them)
declared in `.claude/settings*.json`. Claude Code keeps one marketplace
registry per user, so every checkout and worktree claimed the same `meow-*`
names and broke the others ("Marketplace file not found"). Those plugins now
live in `.claude/skills/meow-<scope>/` (`sources.scope_plugin_dir`).

The old layout is migrated, not supported: MEOW no longer reads it. Migration
runs before configuration loads (any MEOW command's onboarding check, `meow
sync`, and the plugin's startup sync), so a config naming an old folder is
rewritten before it could fail.
"""

import json
import shutil
from pathlib import Path

import tomlkit

from meow.common.atomic import set_aside
from meow.project.custom.sources import legacy_plugin_dir, scope_plugin_dir

LEGACY_MARKETPLACES = (
    "meow-project",
    "meow-local",
    "meow-project-linked",
    "meow-local-linked",
)
_SETTINGS = ("settings.json", "settings.local.json")
_CONFIGS = (".meow/config.toml", ".meow/config.local.toml")
_HINT = (
    "if Claude Code still reports these marketplaces, run "
    "`claude plugin marketplace remove NAME` for each once"
)


def _prune(directory: Path) -> None:
    """Remove `directory` and its parents up to `.meow` while they are empty."""
    while directory.name not in {".meow", ""} and directory.is_dir():
        try:
            directory.rmdir()
        except OSError:
            return
        directory = directory.parent


def _move_skills(old: Path, new: Path) -> list[str]:
    """Move each skill folder over; returns the names left behind (taken)."""
    kept = []
    folders = sorted((old / "skills").glob("*")) if (old / "skills").is_dir() else []
    for item in folders:
        target = new / "skills" / item.name
        if target.exists():
            kept.append(item.name)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(item), str(target))
    return kept


def _move_hooks(old: Path, new: Path) -> None:
    """Carry a hooks.json over (hand-written entries survive the next sync)."""
    hooks = old / "hooks" / "hooks.json"
    if hooks.is_file() and not (new / "hooks" / "hooks.json").exists():
        (new / "hooks").mkdir(parents=True, exist_ok=True)
        shutil.move(str(hooks), str(new / "hooks" / "hooks.json"))


def _drop_manifests(old: Path) -> None:
    (old / ".claude-plugin" / "plugin.json").unlink(missing_ok=True)
    for folder in (old / ".claude-plugin", old / "skills", old / "hooks"):
        _prune(folder)
    marketplace = old.parent / ".claude-plugin" / "marketplace.json"
    marketplace.unlink(missing_ok=True)
    _prune(marketplace.parent)


def _move_plugin(scope: str, project_dir: Path, apply: bool) -> list[str]:
    old = legacy_plugin_dir(scope, project_dir)
    if not old.is_dir():
        return []
    if not apply:
        return [f"{scope} plugin ({old}): old layout; run `meow sync` to move it"]
    new = scope_plugin_dir(scope, project_dir, project_dir)
    kept = _move_skills(old, new)
    _move_hooks(old, new)
    _drop_manifests(old)
    line = f"{scope} plugin: moved from {old} to {new}"
    if kept:
        line += f"; kept {', '.join(kept)} in {old}: a skill by that name exists"
    return [line]


def _drop_linked(scope: str, project_dir: Path, apply: bool) -> list[str]:
    old = legacy_plugin_dir(scope, project_dir, linked=True).parent
    if not old.is_dir():
        return []
    if not apply:
        return [f"{scope} linked copies ({old}): old layout; rebuilt elsewhere"]
    backup = set_aside(old)
    return [f"{scope} linked copies ({old}): old layout; kept as {backup}"]


def _ours(entry: object) -> bool:
    """A marketplace entry MEOW declared: a directory source."""
    source = entry.get("source") if isinstance(entry, dict) else None
    return isinstance(source, dict) and source.get("source") == "directory"


def _table(data: dict, key: str) -> dict:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def _stale(data: dict) -> list[str]:
    known, enabled = (
        _table(data, "extraKnownMarketplaces"),
        _table(data, "enabledPlugins"),
    )
    return [
        market
        for market in LEGACY_MARKETPLACES
        if _ours(known.get(market)) or f"{market}@{market}" in enabled
    ]


def _without(data: dict, stale: list[str]) -> dict:
    """`data` minus the stale marketplaces and their enabled plugins."""
    known = {
        name: entry
        for name, entry in _table(data, "extraKnownMarketplaces").items()
        if not (name in stale and _ours(entry))
    }
    enabled = {
        key: value
        for key, value in _table(data, "enabledPlugins").items()
        if key not in {f"{market}@{market}" for market in stale}
    }
    result = {
        k: v
        for k, v in data.items()
        if k not in {"extraKnownMarketplaces", "enabledPlugins"}
    }
    if known:
        result["extraKnownMarketplaces"] = known
    if enabled:
        result["enabledPlugins"] = enabled
    return result


def _clean_settings(project_dir: Path, apply: bool) -> list[str]:
    found = []
    for name in _SETTINGS:
        path = Path(project_dir) / ".claude" / name
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        stale = _stale(data) if isinstance(data, dict) else []
        if not stale:
            continue
        if not apply:
            found.append(f".claude/{name}: old marketplaces {', '.join(stale)}")
            continue
        cleaned = _without(data, stale)
        if cleaned:
            path.write_text(json.dumps(cleaned, indent=2) + "\n", "utf-8")
        else:
            path.unlink()  # MEOW's entries were all it held
        found.append(
            f".claude/{name}: removed old marketplaces {', '.join(stale)}; {_HINT}"
        )
    return found


def _new_path(raw: str, project_dir: Path) -> str | None:
    """The new location for a `[custom].skills` path naming an old folder."""
    text = raw.replace("\\", "/").removeprefix("./").rstrip("/")
    for scope in ("project", "local"):
        old = legacy_plugin_dir(scope, project_dir) / "skills"
        if text == old.relative_to(project_dir).as_posix() or (
            Path(raw).expanduser().is_absolute()
            and Path(raw).expanduser().resolve() == old.resolve()
        ):
            new = scope_plugin_dir(scope, project_dir, project_dir) / "skills"
            return new.relative_to(project_dir).as_posix()
    return None


def _rewrite_entries(entries, project_dir: Path) -> bool:
    """Point entries at the new folders in place; True if any changed."""
    changed = False
    for index, item in enumerate(entries):
        raw = item.get("path") if hasattr(item, "get") else item
        new = _new_path(str(raw), project_dir) if isinstance(raw, str) else None
        if new is None:
            continue
        if hasattr(item, "get"):
            item["path"] = new
        else:
            entries[index] = new
        changed = True
    return changed


def _rewrite_configs(project_dir: Path, apply: bool) -> list[str]:
    found = []
    for name in _CONFIGS:
        path = Path(project_dir) / name
        try:
            document = tomlkit.parse(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        custom = document.get("custom")
        entries = custom.get("skills") if hasattr(custom, "get") else None
        if not isinstance(entries, list) or not _rewrite_entries(entries, project_dir):
            continue
        found.append(f"{name}: [custom].skills now names the new plugin folders")
        if apply:
            path.write_text(tomlkit.dumps(document), encoding="utf-8")
    return found


def legacy_layout(project_dir: Path, apply: bool = False) -> list[str]:
    """Describe (and with `apply`, migrate) everything in the old layout."""
    project_dir = Path(project_dir)
    found = _rewrite_configs(project_dir, apply)
    for scope in ("project", "local"):
        found += _move_plugin(scope, project_dir, apply)
        found += _drop_linked(scope, project_dir, apply)
    return found + _clean_settings(project_dir, apply)
