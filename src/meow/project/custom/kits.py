"""Kits: git repositories of skills, agents, workflows, and hooks.

`[[kits]]` entries in any config layer name a kit; the layer's scope is the
kit's scope. A lock file per scope pins each kit to a commit, and the cache in
`kit_store` holds those commits. Loading configuration reads only the lock and
the cache, never the network.
"""

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from meow.project.custom.kit_store import kit_dir, normalize_url
from meow.project.custom.sources import KINDS, SKILL_ROLES, CustomSource

MANIFEST = "meow-kit.toml"
NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
UPDATE_MODES = ("auto", "pinned")
DEFAULT_CHECK_SECONDS = 6 * 3600
_KEYS = {
    "name", "url", "ref", "update", "auto_accept_hooks", "only", "config",
    "check_every",
}  # fmt: skip
_MANIFEST_KEYS = {"name", "description", "skills", "config"}
# Kit config may only set values that run no code and open no gates.
_CONFIG_ALLOWED = {
    "workflow": {"default"},
    "models": None,  # any role -> model name
    "worktree_setup": {"install_timeout"},
}
_UNITS = {"m": 60, "h": 3600, "d": 86400}


@dataclass(frozen=True)
class KitSpec:
    name: str
    url: str
    scope: str
    config_file: Path
    ref: str | None = None
    update: str = "auto"
    auto_accept_hooks: bool = False
    only: tuple[str, ...] = ()
    config: bool = False
    check_every: int = DEFAULT_CHECK_SECONDS


def _duration(value: object, where: str) -> int:
    match = re.fullmatch(r"(\d+)([mhd])", str(value))
    if not match:
        raise ValueError(f"{where}: check_every must look like '30m', '6h' or '1d'")
    return int(match[1]) * _UNITS[match[2]]


def parse_kits(entries: object, scope: str, config_file: Path) -> list[KitSpec]:
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise ValueError(f"{config_file}: [[kits]] must be an array of tables")
    specs: list[KitSpec] = []
    for position, raw in enumerate(entries, 1):
        where = f"{config_file}: [[kits]] entry {position}"
        if not isinstance(raw, dict):
            raise ValueError(f"{where} must be a table")
        if unknown := sorted(set(raw) - _KEYS):
            raise ValueError(f"{where} has unknown key(s) {unknown}")
        url = raw.get("url")
        if not isinstance(url, str):
            raise ValueError(f"{where} needs a 'url'")
        normalize_url(url)
        name = raw.get("name")
        if not isinstance(name, str) or not NAME.match(name):
            raise ValueError(
                f"{where}: 'name' must be lowercase letters/digits/hyphens"
            )
        if any(spec.name == name for spec in specs):
            raise ValueError(f"{where}: kit {name!r} is declared twice in this file")
        update = raw.get("update", "auto")
        if update not in UPDATE_MODES:
            raise ValueError(f"{where}: 'update' must be one of {UPDATE_MODES}")
        only = tuple(raw.get("only", ()))
        if not set(only) <= set(KINDS):
            raise ValueError(f"{where}: 'only' may list {list(KINDS)}")
        ref = raw.get("ref")
        if ref is not None and (not isinstance(ref, str) or ref.startswith("-")):
            raise ValueError(f"{where}: invalid 'ref'")
        specs.append(
            KitSpec(
                name, url, scope, config_file, ref, update,
                bool(raw.get("auto_accept_hooks", False)) and scope != "project",
                only, bool(raw.get("config", False)),
                _duration(raw["check_every"], where)
                if "check_every" in raw
                else DEFAULT_CHECK_SECONDS,
            )
        )  # fmt: skip
    return specs


def lock_file(scope: str, project_dir: Path, user_dir: Path) -> Path:
    if scope == "user":
        return Path(user_dir) / "kits.lock"
    sub = Path(".meow") / ("local" if scope == "local" else "")
    return Path(project_dir) / sub / "kits.lock"


def read_lock(path: Path) -> dict[str, dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        kits = data.get("kits", {})
    except (OSError, ValueError, AttributeError):
        return {}
    return kits if isinstance(kits, dict) else {}


def write_lock(path: Path, kits: dict[str, dict]) -> None:
    if not kits:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"kits": kits}, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")


def kits_home(user_dir: Path) -> Path:
    return Path(user_dir) / "kits"


def read_manifest(root: Path, only: tuple[str, ...] = ()) -> dict:
    """Validate and return `meow-kit.toml` (empty when the kit has none)."""
    path = root / MANIFEST
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"{path}: {exc}") from exc
    if unknown := sorted(set(data) - _MANIFEST_KEYS):
        raise ValueError(f"{path}: unknown key(s) {unknown}")
    roles = data.get("skills", {})
    if not isinstance(roles, dict) or set(roles) - {"roles"}:
        raise ValueError(f"{path}: [skills] accepts only [skills.roles]")
    for skill, wanted in roles.get("roles", {}).items():
        bad = sorted(set(wanted) - SKILL_ROLES) if isinstance(wanted, list) else None
        if bad is None or bad:
            raise ValueError(f"{path}: [skills.roles].{skill}: unknown role(s) {bad}")
        if not (root / "skills" / skill / "SKILL.md").is_file() and (
            not only or "skills" in only
        ):
            raise ValueError(f"{path}: [skills.roles] names missing skill {skill!r}")
    _check_config(data.get("config", {}), path)
    return data


def _check_config(config: object, path: Path) -> None:
    if not isinstance(config, dict):
        raise ValueError(f"{path}: [config] must be a table")
    for table, values in config.items():
        if table not in _CONFIG_ALLOWED or not isinstance(values, dict):
            raise ValueError(
                f"{path}: kits may set only {sorted(_CONFIG_ALLOWED)} in [config]; "
                f"{table!r} runs code or changes gates, so set it in your own config"
            )
        allowed = _CONFIG_ALLOWED[table]
        if allowed is not None and (extra := sorted(set(values) - allowed)):
            raise ValueError(f"{path}: [config.{table}] may not set {extra}")


def validate_kit(root: Path, only: tuple[str, ...] = ()) -> dict:
    """Check a fetched kit before a lock file adopts it."""
    manifest = read_manifest(root, only)
    if not any((root / kind).is_dir() for kind in KINDS) and not manifest:
        raise ValueError(f"{root.name}: no skills/, agents/, workflows/ or hooks/")
    return manifest


def contents(root: Path) -> dict[str, list[str]]:
    found = {}
    for kind in KINDS:
        folder = root / kind
        if folder.is_dir():
            found[kind] = sorted(
                p.stem if p.is_file() else p.name
                for p in folder.iterdir()
                if not p.name.startswith(".")
            )
    return found


def _locked_root(
    spec: KitSpec, entry: dict | None, home: Path, key: str
) -> Path | None:
    sha = (entry or {}).get(key) or (entry or {}).get("sha")
    if not sha:
        return None
    root = kit_dir(home, normalize_url(spec.url), sha)
    return root if root.is_dir() else None


def kit_sources(
    spec: KitSpec, entry: dict | None, home: Path
) -> tuple[list[CustomSource], dict]:
    """Sources and allowed config for one kit; a placeholder when not fetched."""
    root = _locked_root(spec, entry, home, "sha")
    if root is None:
        placeholder = CustomSource(
            "skills", home / spec.name, spec.scope, spec.config_file,
            missing=True, kit=spec.name,
        )  # fmt: skip
        return [placeholder], {}
    manifest = read_manifest(root, spec.only)
    hooks_root = _locked_root(spec, entry, home, "hooks_sha") or root
    roles = tuple(
        (skill, tuple(wanted))
        for skill, wanted in manifest.get("skills", {}).get("roles", {}).items()
    )
    sources = []
    for kind in KINDS:
        base = hooks_root if kind == "hooks" else root
        if (not spec.only or kind in spec.only) and (base / kind).is_dir():
            sources.append(
                CustomSource(
                    kind,
                    base / kind,
                    spec.scope,
                    None,
                    kit=spec.name,
                    skill_roles=roles if kind == "skills" else (),
                    project_dir=base,
                )  # fmt: skip
            )
    return sources, (manifest.get("config", {}) if spec.config else {})


def layer_kits(
    raw: object, scope: str, config_file: Path, project_dir: Path, user_dir: Path
) -> tuple[list[CustomSource], dict]:
    """Sources and allowed config from one config layer's `[[kits]]`."""
    specs = parse_kits(raw, scope, config_file)
    if not specs:
        return [], {}
    locked = read_lock(lock_file(scope, project_dir, user_dir))
    sources: list[CustomSource] = []
    config: dict = {}
    for spec in specs:
        found, kit_config = kit_sources(
            spec, locked.get(spec.name), kits_home(user_dir)
        )
        sources += found
        config = _deep(config, kit_config)
    return sources, config


def _deep(base: dict, over: dict) -> dict:
    merged = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep(merged[key], value)
        else:
            merged[key] = value
    return merged
