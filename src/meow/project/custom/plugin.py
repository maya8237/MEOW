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

_MANIFEST = Path(".claude-plugin") / "plugin.json"


def _cache_root() -> Path:
    return Path.home() / ".meow" / "cache" / "custom-skills"


def _files(skill: CustomSkill) -> list[Path]:
    return sorted(path for path in skill.directory.rglob("*") if path.is_file())


def _digest(skills: tuple[CustomSkill, ...]) -> str:
    digest = hashlib.sha256()
    for skill in skills:
        for path in _files(skill):
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
