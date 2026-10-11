"""Repos the catalog page can save into, remembered in the user's MEOW config."""

import json
import tomllib
from contextlib import suppress
from pathlib import Path

from meow.project.config import user_config_path


def registry_path() -> Path:
    return user_config_path().parent / "repos.toml"


def repo_choices(working_dir: Path) -> list[str]:
    current = str(Path(working_dir).resolve())
    return [current, *(repo for repo in _saved() if repo != current)]


def remember(repo: Path) -> None:
    current = str(Path(repo).resolve())
    saved = _saved()
    if current in saved:
        return
    saved.append(current)
    body = "".join(f"  {json.dumps(path)},\n" for path in saved)
    path = registry_path()
    with suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"repos = [\n{body}]\n", encoding="utf-8", newline="\n")


def _saved() -> list[str]:
    path = registry_path()
    if not path.exists():
        return []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return []
    repos = data.get("repos", [])
    if not isinstance(repos, list):
        return []
    return [repo for repo in repos if isinstance(repo, str) and Path(repo).is_dir()]
