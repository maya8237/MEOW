"""Serve custom skills to the Agent SDK as one synthesized local plugin.

The SDK loads skills from plugin directories, so the configured skill
directories are copied into a content-addressed plugin under
`~/.meow/cache/custom-skills/`. Identical content reuses the same directory,
and a build is renamed into place atomically, so concurrent runs never see a
half-written plugin.
"""

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from meow.project.custom.definitions import PLUGIN_NAME, CustomSkill
from meow.project.custom.sources import source_files

_MANIFEST = Path(".claude-plugin") / "plugin.json"


def _cache_root() -> Path:
    return Path.home() / ".meow" / "cache" / "custom-skills"


def _digest(skills: tuple[CustomSkill, ...]) -> str:
    digest = hashlib.sha256()
    for skill in skills:
        for path in source_files(skill.directory, skill.source):
            relative = path.relative_to(skill.directory).as_posix()
            digest.update(f"{skill.name}/{relative}\0".encode())
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()[:16]


def _build(skills: tuple[CustomSkill, ...], target: Path) -> None:
    manifest = target / _MANIFEST
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps({
            "name": PLUGIN_NAME,
            "description": "Custom skills configured in MEOW [custom] tables.",
            "version": "0.0.0",
        }),
        encoding="utf-8",
    )
    for skill in skills:
        source_files(skill.directory, skill.source)
        shutil.copytree(skill.directory, target / "skills" / skill.name)


def skill_plugin_dir(skills: tuple[CustomSkill, ...]) -> Path | None:
    """The plugin directory serving `skills`, built on first use."""
    if not skills:
        return None
    root = _cache_root()
    final = root / _digest(skills)
    if (final / _MANIFEST).is_file():
        return final
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".build-", dir=root))
    try:
        _build(skills, staging / "plugin")
        try:
            os.replace(staging / "plugin", final)
        except OSError:
            # Another run finished the same build first.
            if not (final / _MANIFEST).is_file():
                raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return final


_SCOPE_PLUGIN_NAMES = {
    "user": "meow-user",
    "project": "meow-project",
    "local": "meow-local",
}


def plugin_name(scope: str, *, linked: bool = False) -> str:
    """The Claude plugin name for a scope; `linked` serves `[custom].skills`."""
    name = _SCOPE_PLUGIN_NAMES[scope]
    return f"{name}-linked" if linked else name


def ensure_plugin_root(root: Path, scope: str, *, linked: bool = False) -> Path:
    """Create a scope's Claude plugin root: its manifest and `skills/` folder."""
    (root / "skills").mkdir(parents=True, exist_ok=True)
    manifest = root / _MANIFEST
    if not manifest.is_file():
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps(
                {
                    "name": plugin_name(scope, linked=linked),
                    "description": f"MEOW {scope} skills.",
                    "version": "0.1.0",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return root


def ensure_marketplace(base: Path, scope: str, *, linked: bool = False) -> None:
    """Write the marketplace file that lists the scope's plugin under `base`."""
    path = Path(base) / ".claude-plugin" / "marketplace.json"
    if path.is_file():
        return
    name = plugin_name(scope, linked=linked)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "name": name,
                "owner": {"name": "MEOW"},
                "plugins": [
                    {
                        "name": name,
                        "source": "./plugin",
                        "description": f"MEOW {scope} skills.",
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
