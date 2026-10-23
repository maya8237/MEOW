"""Inspect, validate, compare, and eject resolved TOML definitions."""

import difflib
import hashlib
from pathlib import Path

from meow.project.config import load_config, user_config_path
from meow.project.custom.definitions import discover
from meow.project.custom.diagnostics import warn_definition, warn_workflow
from meow.project.custom.sources import builtin_sources, conventional_dir
from meow.project.custom.workflows import validate


def _lookup(catalog, kind, name):
    try:
        return catalog.agent(name) if kind == "agents" else catalog.workflow(name)
    except KeyError as exc:
        raise ValueError(str(exc)) from exc


def _shipped(kind, name):
    return _lookup(discover(builtin_sources()), kind, name)


def eject(kind, name, scope, project_dir):
    """Copy the shipped file exclusively, preserving its origin hash."""
    item = _shipped(kind, name)
    directory = conventional_dir(scope, kind, project_dir, user_config_path().parent)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / item.file.name
    if target.with_suffix(".md").exists():
        raise FileExistsError(
            f"Refusing to overwrite definition {target.with_suffix('.md')}"
        )
    data = item.file.read_bytes()
    stamp = hashlib.sha256(data).hexdigest()
    with target.open("x", encoding="utf-8", newline="\n") as output:
        output.write(f'ejected_from = "builtin:{name}@{stamp}"\n\n')
        output.write(data.decode("utf-8"))
    return target


def _list(items, catalog):
    for item in sorted(items, key=lambda item: item.name):
        print(f"{item.name} ({item.source.scope}): {item.description}")
    for message in catalog.overrides:
        print(message)


def _validate(items, catalog):
    problems = []
    for item in items:
        warn_workflow(item, catalog)
        errors = validate(item, catalog)
        problems.extend(errors)
        if not errors:
            print(f"{item.name}: valid")
    if problems:
        raise ValueError("\n".join(problems))


def _warn(item, catalog, kind):
    if kind == "workflows":
        warn_workflow(item, catalog)
    else:
        warn_definition(item, kind)


def catalog_command(args, project_dir: Path) -> None:
    kind, action = args.command, args.catalog_action
    if action == "eject":
        print(eject(kind, args.name, args.scope, project_dir))
        return
    catalog = load_config(project_dir)["customizations"]
    items = catalog.catalog if kind == "agents" else catalog.workflows
    if action == "list":
        _list(items, catalog)
        return
    if action == "validate":
        selected = [_lookup(catalog, kind, args.name)] if args.name else items
        _validate(selected, catalog)
        return
    item = _lookup(catalog, kind, args.name)
    _warn(item, catalog, kind)
    text = item.file.read_text(encoding="utf-8")
    if action == "show":
        print(f"# {item.name} ({item.source.scope}): {item.file}\n{text}")
    elif action == "diff":
        shipped = _shipped(kind, args.name)
        print(
            "".join(
                difflib.unified_diff(
                    shipped.file.read_text(encoding="utf-8").splitlines(keepends=True),
                    text.splitlines(keepends=True),
                    fromfile=str(shipped.file),
                    tofile=str(item.file),
                )
            ),
            end="",
        )


def add_catalog_parsers(subparsers):
    for kind in ("workflows", "agents"):
        parser = subparsers.add_parser(kind, help=f"Inspect and eject {kind}.")
        actions = parser.add_subparsers(dest="catalog_action", required=True)
        for action in (
            "list",
            "show",
            "eject",
            "validate" if kind == "workflows" else "diff",
        ):
            entry = actions.add_parser(action)
            if action != "list":
                entry.add_argument("name", nargs="?" if action == "validate" else None)
            if action == "eject":
                entry.add_argument(
                    "--scope", choices=("local", "project", "user"), default="project"
                )
            entry.add_argument("--work-dir", "-d", dest="working_dir", default=".")
