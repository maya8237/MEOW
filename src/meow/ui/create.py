"""Add an agent, workflow, or skill at a chosen scope."""

import json
import re
import shutil
import tomllib
from pathlib import Path

from meow.project.config import user_config_path
from meow.project.custom.definitions import discover, load_agent
from meow.project.custom.sources import CustomSource, conventional_dir, git_ignored
from meow.project.custom.workflows import load_workflow
from meow.project.repos import repo_choices
from meow.ui.catalog import build_catalog

SCOPES = ("project", "local", "user")
_NAME = re.compile(r"[a-z][a-z0-9_-]{0,62}")
_IDENTITY = re.compile(
    r"^(?:name|description|ejected_from)\s*="
    r"(?:\s*\"\"\"[\s\S]*?\"\"\"|\s*'''[\s\S]*?'''|[^\n]*)\n?",
    re.MULTILINE,
)
_TABLE_START = re.compile(r"(?m)^(?=\[)")
_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n?", re.DOTALL)


class CreateError(ValueError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def create_definition(working_dir: Path, kind: str, payload: dict) -> dict:
    name, description, source, scope = _request(payload)
    working_dir = _repo(payload, working_dir)
    catalog = build_catalog(working_dir)
    if catalog["error"]:
        raise CreateError(422, catalog["error"])
    items = {item["name"]: item for item in catalog[kind]}
    if name in items:
        raise CreateError(409, f"A {kind[:-1]} named {name} already exists.")
    item = _source(kind, items, source)
    if kind == "skills":
        folder, register = _skill_location(scope, working_dir)
        created = _create_skill((folder, scope), name, description, item)
        if register:
            _register_folder(*register)
        return created
    directory = conventional_dir(scope, kind, working_dir, user_config_path().parent)
    text = _with_identity(item["text"], name, description)
    return _create_file(directory / f"{name}.toml", kind, scope, text)


def _request(payload: dict) -> tuple[str, str, str, str]:
    name = payload.get("name")
    description = payload.get("description", "")
    source = payload.get("source", "")
    scope = payload.get("scope", "project")
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise CreateError(
            400,
            "Use a name that starts with a letter and holds only lowercase "
            "letters, digits, '-' or '_'.",
        )
    if not isinstance(description, str) or not isinstance(source, str):
        raise CreateError(400, "Description and source must be text.")
    if not description.strip():
        raise CreateError(400, "Add a description.")
    if scope not in SCOPES:
        raise CreateError(400, "Scope must be project, local, or user.")
    return name, description, source, scope


def _repo(payload: dict, working_dir: Path) -> Path:
    repo = payload.get("repo") or str(Path(working_dir).resolve())
    if not isinstance(repo, str) or repo not in repo_choices(working_dir):
        raise CreateError(400, "Choose a repo from the list.")
    return Path(repo)


def _source(kind: str, items: dict, source: str) -> dict | None:
    if not source:
        if kind == "skills":
            return None
        raise CreateError(400, f"Choose a {kind[:-1]} to copy.")
    if source not in items:
        raise CreateError(400, f"Unknown {kind[:-1]} to copy: {source}.")
    return items[source]


def _create_file(target: Path, kind: str, scope: str, text: str) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("x", encoding="utf-8", newline="\n") as output:
            output.write(text)
    except FileExistsError as exc:
        raise CreateError(409, f"{target} already exists.") from exc
    try:
        _load(kind, target, scope)
    except ValueError as exc:
        target.unlink()
        raise CreateError(422, str(exc)) from exc
    return {"name": target.stem, "file": str(target)}


def _create_skill(
    where: tuple[Path, str], name: str, description: str, item: dict | None
) -> dict:
    folder, scope = where
    target = folder / name
    if target.exists():
        raise CreateError(409, f"{target} already exists.")
    if item:
        shutil.copytree(Path(item["directory"]), target)
        body = _FRONTMATTER.sub("", item["text"], count=1)
    else:
        target.mkdir(parents=True)
        body = f"\n# {name}\n\nDescribe when to use this skill and what it should do.\n"
    skill_file = target / "SKILL.md"
    skill_file.write_text(
        f"---\nname: {name}\ndescription: {json.dumps(description)}\n---\n{body}",
        encoding="utf-8",
        newline="\n",
    )
    try:
        loaded = discover([CustomSource("skills", folder, scope, None)])
    except ValueError as exc:
        shutil.rmtree(target)
        raise CreateError(422, str(exc)) from exc
    if name not in {skill.name for skill in loaded.skills}:
        shutil.rmtree(target)
        raise CreateError(422, f"{name} did not load as a skill.")
    return {"name": name, "file": str(skill_file)}


def _scope_config(scope: str, working_dir: Path) -> tuple[Path, Path]:
    """Config file for a scope, and the base its relative paths resolve from."""
    if scope == "user":
        config = user_config_path()
        return config, config.parent
    if scope == "local":
        return working_dir / ".meow" / "config.local.toml", working_dir
    return working_dir / ".meow" / "config.toml", working_dir


def _skill_location(
    scope: str, working_dir: Path
) -> tuple[Path, tuple[Path, str] | None]:
    """Skill folder for a scope, and the `[custom]` entry to add if missing."""
    config, base = _scope_config(scope, working_dir)
    data = tomllib.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
    custom = data.get("custom")
    if custom is None:
        entry = {
            "user": "skills",
            "project": ".meow/skills",
            "local": ".meow/local/skills",
        }[scope]
        folder, register = base / entry, (config, entry)
    else:
        folders = custom.get("skills")
        if (
            not isinstance(folders, list)
            or not folders
            or not isinstance(folders[0], str)
        ):
            raise CreateError(
                422,
                f"Add a skills = [...] entry under [custom] in {config} first.",
            )
        folder, register = base / folders[0], None
    if scope == "project" and git_ignored(base, folder):
        raise CreateError(
            422,
            f"{folder} is ignored by git, so project skills there would not be "
            "shared. Save to Local instead, or un-ignore that folder.",
        )
    return folder, register


def _register_folder(config: Path, entry: str) -> None:
    config.parent.mkdir(parents=True, exist_ok=True)
    with config.open("a", encoding="utf-8", newline="\n") as output:
        output.write(f'\n[custom]\nskills = ["{entry}"]\n')


def _with_identity(text: str, name: str, description: str) -> str:
    head, *tail = _TABLE_START.split(text, maxsplit=1)
    head = _IDENTITY.sub("", head).strip()
    top = f'name = "{name}"\ndescription = {json.dumps(description)}\n'
    if head:
        top += f"\n{head}\n"
    return top + ("\n" + tail[0] if tail else "")


def _load(kind: str, path: Path, scope: str) -> None:
    source = CustomSource(kind, path.parent, scope, None)
    if kind == "workflows":
        load_workflow(path, source)
    else:
        load_agent(path, source)
