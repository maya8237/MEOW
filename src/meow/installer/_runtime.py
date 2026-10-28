"""Implementation for the streamed MEOW installer post-install setup."""

from __future__ import annotations

import argparse
import glob
import os
from collections.abc import Callable, Sequence
from pathlib import Path

from meow.installer._prompt import path_prompt
from meow.installer.bun import (
    configure_bunfig,
    migrate_bun_projects,
    shared_store_configured,
    start_bun_migration,
)
from meow.project.claude_settings import path_key, register_checkout_plugin
from meow.project.mcp_setup import (
    SPECS,
    describe,
    hidden_prompt,
    parse_providers,
    setup_provider,
    setup_providers,
)
from meow.project.onboarding import feature_gaps, onboard_project
from meow.project.repos import _saved, remember

NEWLINE = chr(10)
EXIT_INTERRUPTED = 130


def _yes(answer: str) -> bool:
    return answer.strip().lower() in {"y", "yes"}


def _first_level_pattern_is_safe(pattern: str, output: Callable[[str], None]) -> bool:
    """Reject a non-recursive glob that would span multiple path components."""
    parts = Path(pattern).parts
    wildcard_positions = [
        position for position, part in enumerate(parts) if glob.has_magic(part)
    ]
    if wildcard_positions and wildcard_positions[-1] != len(parts) - 1:
        output(
            "`*` is limited to immediate child folders; use a final `*` path component."
        )
        return False
    return len(wildcard_positions) <= 1


def _sorted_directories(pattern: str, *, recursive: bool) -> tuple[Path, ...]:
    matches = glob.glob(pattern, recursive=recursive)
    paths = {Path(match).expanduser().resolve() for match in matches}
    if recursive:
        prefix = pattern.split("**", 1)[0].rstrip("/\\")
        paths.discard(Path(prefix or ".").resolve())
    return tuple(sorted((path for path in paths if path.is_dir()), key=path_key))


def _resolve_recursive_pattern(
    pattern: str,
    *,
    ask: Callable[[str], str],
    output: Callable[[str], None],
) -> tuple[str, bool] | None:
    output(
        "`*` matches only immediate child folders; `**` matches folders "
        "recursively, including nested projects."
    )
    if _yes(ask("Did you mean `*` instead? [Y/n] ")):
        return pattern.replace("**", "*"), False
    if not _yes(ask("Continue with recursive onboarding using `**`? [y/N] ")):
        output("Skipping this recursive pattern.")
        return None
    return pattern, True


def _literal_or_glob_matches(
    pattern: str, *, recursive: bool, output: Callable[[str], None]
) -> tuple[Path, ...]:
    if not glob.has_magic(pattern):
        path = Path(pattern).resolve()
        if path.is_dir():
            return (path,)
        output(f"No project directory exists at: {pattern}")
        return ()

    matches = _sorted_directories(pattern, recursive=recursive)
    if not matches:
        output(f"No project directories matched: {pattern}")
    return matches


def expand_project_pattern(
    pattern: str,
    *,
    ask: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
) -> tuple[Path, ...]:
    """Expand one literal or glob project destination with recursive safeguards."""
    pattern = os.path.expandvars(os.path.expanduser(pattern.strip().strip('"')))
    if not pattern:
        return ()

    recursive = False
    if "**" in pattern:
        resolved = _resolve_recursive_pattern(pattern, ask=ask, output=output)
        if resolved is None:
            return ()
        pattern, recursive = resolved
    if (
        not recursive
        and "*" in pattern
        and not _first_level_pattern_is_safe(pattern, output)
    ):
        return ()
    return _literal_or_glob_matches(pattern, recursive=recursive, output=output)


def _settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _print_report(path: Path, report, output: Callable[[str], None]) -> None:
    if report.error:
        output(f"{path}: onboarding skipped ({report.error})")
        return
    changed = ", ".join(report.files) or "no files changed"
    lint = report.lint or "none detected"
    output(f"{path}: onboarded ({changed}); lint: {lint}")


def _print_features(project: Path, output: Callable[[str], None]) -> None:
    gaps = feature_gaps(project)
    configured = [
        key.replace("_", " ") for key, gap in gaps.items() if gap["configured"]
    ]
    available = [
        key.replace("_", " ") for key, gap in gaps.items() if not gap["configured"]
    ]
    if configured:
        output(f"  Already configured: {', '.join(configured)} (not verified).")
    if available:
        output(f"  Available add-ons: {', '.join(available)}.")
        output(
            "  Run `claude /meow:onboard` in this project to configure chosen add-ons."
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Register MEOW for Claude Code; optionally set up projects."
    )
    parser.add_argument(
        "--repo-dir",
        help="Absolute or relative path to the cloned MEOW checkout.",
    )
    parser.add_argument(
        "--next-steps",
        action="store_true",
        help="Only print the closing guidance (the scripts show it after Done!).",
    )
    parser.add_argument("--migrate-bun", nargs="+", help=argparse.SUPPRESS)
    parser.add_argument("--update-bun", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--setup", action="store_true", help="Offer optional Bun and project setup."
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Skip all optional setup prompts.",
    )
    parser.add_argument(
        "--mcp",
        metavar="PROVIDERS",
        help=(
            "Register user-scope MCP servers (jira,github or all) without "
            "prompting; credentials come from the servers' own environment "
            "variables, e.g. JIRA_URL and JIRA_API_TOKEN."
        ),
    )
    return parser


def _register_plugin(repo_dir: Path, output: Callable[[str], None]) -> bool:
    try:
        register_checkout_plugin(_settings_path(), repo_dir)
    except (OSError, ValueError) as exc:
        output(f"Could not update Claude user settings: {exc}")
        return False
    output(
        f"Registered MEOW as a user-wide Claude plugin: {repo_dir}\n"
        "Restart Claude Code to load /meow:* skills (including /meow:run)."
    )
    return True


def _example_path(leaf: str) -> str:
    root = "C:\\Projects" if os.name == "nt" else "/home/user/projects"
    return root + os.sep + leaf


def _wants_projects_now(
    ask: Callable[[str], str], output: Callable[[str], None]
) -> bool:
    try:
        answer = ask(
            "Refresh registered MEOW projects and set up new ones now? "
            "You can also do it later with "
            "`claude /meow:onboard`. [Y/n]: "
        )
    except EOFError:
        output(
            "\nNo input available; skipped project setup. "
            "Run `claude /meow:onboard` in a project to set it up later."
        )
        return False
    return answer.strip().lower() in {"", "y", "yes"}


def _project_patterns(ask_path: Callable[[str], str], output: Callable[[str], None]):
    output(
        "Add projects one at a time "
        f"(Tab autocompletes paths; `{_example_path('*')}` adds every git "
        "repository directly inside it, top level only)"
    )
    while True:
        try:
            raw = ask_path(
                f"Project directory path, e.g. {_example_path('MyApp')} "
                "(Enter on an empty line to finish): "
            ).strip()
        except EOFError:
            output(
                "\nNo input available; finished adding projects. "
                "Run `claude /meow:onboard` in another project to set it up later."
            )
            return
        if not raw:
            return
        yield raw


def _onboard_one(project: Path, output: Callable[[str], None]) -> bool:
    """Onboard one project; True when it was set up (not skipped or failed)."""
    try:
        report = onboard_project(project)
        _print_report(project, report, output)
        if report.error:
            return False
        _print_features(project, output)
    except (OSError, ValueError, RuntimeError) as exc:
        output(f"{project}: onboarding failed ({exc})")
        return False
    remember(project)
    return True


def _project_destinations(ask, output, ask_path):
    yield from (Path(saved).resolve() for saved in _saved())
    for raw in _project_patterns(ask_path or ask, output):
        yield from expand_project_pattern(raw, ask=ask, output=output)


def _onboard_projects(
    ask: Callable[[str], str],
    output: Callable[[str], None],
    ask_path: Callable[[str], str] | None = None,
    onboarded: list[Path] | None = None,
) -> None:
    """Refresh registered repos and entered paths, recording each success once."""
    done = onboarded if onboarded is not None else []
    if not _wants_projects_now(ask, output):
        return
    seen: set[str] = set()
    for project in _project_destinations(ask, output, ask_path):
        key = path_key(project)
        if key in seen:
            continue
        seen.add(key)
        if _onboard_one(project, output):
            done.append(project)


def _print_next_steps(output: Callable[[str], None]) -> None:
    lines = (
        "",
        "Get started:",
        "- Configure Jira, GitLab, GitHub, custom linting and testing with "
        "`claude /meow:onboard` in your project.",
        '- Start from a terminal: `meow run "Add CSV export" --name csv-export '
        "--work-dir <project>`.",
        "- Start from Claude Code in a project: `claude /meow:run Add CSV export`.",
    )
    output(NEWLINE.join(lines))


def _cancelled_message(onboarded: int) -> str:
    if onboarded == 0:
        projects = "no projects were set up"
    else:
        plural = "" if onboarded == 1 else "s"
        projects = f"{onboarded} project{plural} set up before you stopped"
    return (
        "Setup stopped. MEOW is installed and registered with Claude Code; "
        f"{projects}. "
        "Run `claude /meow:onboard` in a project to set up the rest."
    )


def _setup_bun(ask: Callable[[str], str], output: Callable[[str], None]) -> bool:
    try:
        answer = ask(
            "Enable Bun's shared dependency store for worktrees and migrate "
            "onboarded Bun repos? [Y/n]: "
        )
    except EOFError:
        output(
            "\nNo input available; skipped Bun shared dependency store setup "
            "and migration. Rerun the installer interactively to enable it."
        )
        return False
    if answer.strip().lower() not in {"", "y", "yes"}:
        return False
    path = Path.home() / ".bunfig.toml"
    try:
        configure_bunfig(path)
    except (OSError, ValueError) as exc:
        output(f"Could not configure Bun shared storage: {exc}")
        return False
    output(
        f"Configured {path}: isolated linker and globalStore. "
        "Registered and newly onboarded Bun repos will be migrated in the background. "
        "For other repos, "
        "run `bun install --force --linker isolated` in each repo "
        "(add --frozen-lockfile when it has a Bun lockfile). "
        "Project bunfig.toml can override user settings."
    )
    return True


def _migrate_registered(onboarded: Sequence[Path] = ()) -> None:
    """Migrate onboarded and registered repos; nested paths ride with their parent."""
    projects = list(dict.fromkeys([*onboarded, *(Path(repo) for repo in _saved())]))
    start_bun_migration(
        [
            repo
            for repo in projects
            if not any(
                repo != parent and repo.is_relative_to(parent) for parent in projects
            )
        ],
        print,
    )


def _offer_mcp(ask: Callable[[str], str], output: Callable[[str], None]) -> None:
    """Offer each supported MCP server at user scope; never installs launchers."""
    if not _yes(ask("Set up Jira/GitHub MCP servers for Claude (user scope)? [y/N] ")):
        return
    for spec in SPECS.values():
        if _yes(ask(f"  Set up {spec.label}? [y/N] ")):
            result = setup_provider(spec, os.environ, prompt=hidden_prompt)
            output(f"  {describe(result)}")


def _install_mcp(providers: str, output: Callable[[str], None]) -> int:
    """The non-interactive `--mcp` path: nonzero when a request was not met."""
    results = setup_providers(parse_providers(providers), os.environ)
    for result in results:
        output(describe(result))
    return int(any(result.failed for result in results))


def _setup_projects() -> int:
    """Run the project prompts; Ctrl+C cancels the whole installer."""
    onboarded: list[Path] = []
    try:
        _offer_mcp(input, print)
        shared_bun = _setup_bun(input, print)
        _onboard_projects(input, print, path_prompt(), onboarded)
        if shared_bun:
            _migrate_registered(onboarded)
    except KeyboardInterrupt:
        print(NEWLINE + _cancelled_message(len(onboarded)))
        return EXIT_INTERRUPTED
    return 0


def _update_bun() -> int:
    """Explicitly offer the shared store once, then migrate registered repos."""
    if shared_store_configured(Path.home() / ".bunfig.toml"):
        return 0
    try:
        if _setup_bun(input, print):
            _migrate_registered()
    except KeyboardInterrupt:
        print(NEWLINE + "Bun shared store setup skipped.")
    return 0


def _bun_command(args: argparse.Namespace) -> int:
    if args.update_bun:
        return _update_bun() if not args.non_interactive else 0
    migrate_bun_projects([Path(repo) for repo in args.migrate_bun], print)
    print("Bun migration finished. Check the messages above for any failures.")
    return 0


def _optional_setup(args: argparse.Namespace) -> int:
    status = _install_mcp(args.mcp, print) if args.mcp else 0
    if args.setup and not args.non_interactive:
        status = _setup_projects() or status
    return status


def main(argv: Sequence[str] | None = None) -> int:  # ruff: ignore[too-many-return-statements]
    """Register the plugin, running optional setup only when explicitly requested."""
    parser = _parser()
    args = parser.parse_args(argv)
    if args.migrate_bun or args.update_bun:
        return _bun_command(args)
    if args.next_steps:
        _print_next_steps(print)
        return 0
    if not args.repo_dir:
        parser.error("--repo-dir is required")
    repo_dir = Path(args.repo_dir).expanduser().resolve()
    if not repo_dir.is_dir():
        print(f"MEOW checkout does not exist: {repo_dir}")
        return 2
    if not _register_plugin(repo_dir, print):
        return 1
    return _optional_setup(args)
