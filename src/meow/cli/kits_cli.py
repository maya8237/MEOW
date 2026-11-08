"""`meow kits`: install, update, and remove git-hosted kits."""

import sys
from pathlib import Path

from meow.project.config import load_config, user_config_path
from meow.project.custom import kit_author, kit_ops
from meow.project.custom.kit_store import KitUnavailable
from meow.project.custom.kits import UPDATE_MODES
from meow.project.custom.registration import sync_registration
from meow.project.custom.sources import KINDS


def _confirm(question: str) -> bool:
    if not sys.stdin.isatty():
        raise ValueError("not a terminal: review the contents above and pass --yes")
    return input(question).strip().lower() in {"y", "yes"}


def _sync(project_dir: Path, user_dir: Path) -> None:
    """Publish skills and hooks right away; a broken config only warns."""
    try:
        custom = load_config(project_dir)["customizations"]
        for line in sync_registration(custom, project_dir, user_dir):
            print(line)
    except (OSError, ValueError) as exc:
        print(f"Kit saved, but sync failed: {exc}", file=sys.stderr)


def _add(args, project_dir: Path, user_dir: Path) -> None:
    only = tuple(x for x in (args.only or "").split(",") if x)
    if bad := set(only) - set(KINDS):
        raise ValueError(f"--only accepts {', '.join(KINDS)}; got {sorted(bad)}")
    added = kit_ops.install(
        project_dir, user_dir, args.url, scope=args.scope, name=args.name,
        ref=args.ref, update=args.update_mode, only=only,
        auto_accept_hooks=args.auto_accept_hooks, config=args.config,
        confirm=None if args.yes else _confirm,
    )  # fmt: skip
    if added:
        _sync(project_dir, user_dir)


def _update(args, project_dir: Path, user_dir: Path) -> None:
    lines = kit_ops.update(
        project_dir, user_dir, tuple(args.names), check=args.check, accept=args.accept
    )
    print("\n".join(lines))
    if args.check:  # for CI or a pre-push hook: nonzero when a kit is behind
        if any("update available" in line for line in lines):
            raise SystemExit(1)
        return
    _sync(project_dir, user_dir)


def _uninstall(args, project_dir: Path, user_dir: Path) -> None:
    lines = kit_ops.uninstall(
        project_dir, user_dir, args.name, scope=args.scope, keep_cache=args.keep_cache
    )
    print("\n".join(lines))
    _sync(project_dir, user_dir)


def _validate(args, project_dir: Path, user_dir: Path) -> None:
    target = args.target
    if not Path(target).is_dir() and not Path(target).exists():
        _, sha, root = kit_ops.fetch_new(target, args.ref, user_dir)
        print(f"Checking {target} @ {sha[:7]}")
    else:
        root = Path(target)
    report = kit_author.validate_directory(root)
    for line in report.summary:
        print(f"  {line}")
    for line in report.warnings:
        print(f"  warning: {line}")
    for line in report.problems:
        print(f"  error: {line}")
    if report.problems:
        raise SystemExit(1)
    print("Kit is valid.")


def _init(args, project_dir: Path, user_dir: Path) -> None:
    created = kit_author.scaffold(Path(args.directory), args.name)
    print(f"Created a kit in {args.directory}:")
    print("\n".join(f"  {name}" for name in created))
    print(f"Next: cd {args.directory} && meow kits validate .")


_ACTIONS = {
    "validate": _validate,
    "init": _init,
    "add": _add,
    "update": _update,
    "uninstall": _uninstall,
    "remove": _uninstall,
    "list": lambda args, p, u: print("\n".join(kit_ops.listing(p, u))),
    "show": lambda args, p, u: print("\n".join(kit_ops.show(p, u, args.name))),
}


def kits_command(args, project_dir: Path) -> None:
    try:
        _ACTIONS[args.kits_action](args, project_dir, user_config_path().parent)
    except KitUnavailable as exc:
        raise ValueError(f"could not reach the kit's git remote: {exc}") from exc


def _add_options(add) -> None:
    add.add_argument("url")
    add.add_argument("--name")
    add.add_argument("--ref", help="Branch, tag, or commit (default: default branch).")
    add.add_argument("--scope", choices=("user", "project", "local"), default="user")
    add.add_argument("--only", help="Comma-separated: skills,agents,workflows,hooks.")
    add.add_argument(
        "--update", dest="update_mode", choices=UPDATE_MODES, default="auto"
    )
    add.add_argument("--auto-accept-hooks", action="store_true")
    add.add_argument("--config", action="store_true", help="Apply the kit's [config].")
    add.add_argument("--yes", "-y", action="store_true", help="Skip confirmation.")


def _add_author_actions(action) -> None:
    check = action("validate", "Check a kit directory or git URL before publishing.")
    check.add_argument("target", nargs="?", default=".")
    check.add_argument("--ref", help="With a URL: branch, tag, or commit.")
    create = action("init", "Create a starter kit.")
    create.add_argument("directory", nargs="?", default=".")
    create.add_argument("--name")


def add_kits_parser(subparsers):
    parser = subparsers.add_parser(
        "kits", help="Install skills, agents, workflows, and hooks from a git repo."
    )
    actions = parser.add_subparsers(dest="kits_action", required=True)

    def action(name, help_text, **kwargs):
        sub = actions.add_parser(name, help=help_text, **kwargs)
        sub.add_argument("--work-dir", "-d", dest="working_dir", default=".")
        return sub

    _add_options(
        action("add", "Fetch a kit (owner/repo, github:, gitlab:, or a git URL).")
    )
    action("list", "List installed kits.")
    show = action("show", "Show what a kit contains.")
    show.add_argument("name")
    upd = action("update", "Check kits for updates and apply them.")
    upd.add_argument("names", nargs="*")
    upd.add_argument("--check", action="store_true", help="Only report updates.")
    upd.add_argument("--accept", action="store_true", help="Approve held hook changes.")
    _add_author_actions(action)
    rem = action("uninstall", "Remove a kit.", aliases=["remove"])
    rem.add_argument("name")
    rem.add_argument("--scope", choices=("user", "project", "local"))
    rem.add_argument("--keep-cache", action="store_true")
