"""Implementation for the streamed MEOW installer post-install setup."""

from __future__ import annotations

import argparse
import glob
import os
from collections.abc import Callable, Sequence
from pathlib import Path

from meow.installer._prompt import path_prompt
from meow.project.claude_settings import path_key, register_checkout_plugin
from meow.project.onboarding import onboard_project
from meow.project.repos import remember

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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Register MEOW for Claude Code and onboard project directories."
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
    return parser


def _register_plugin(repo_dir: Path, output: Callable[[str], None]) -> bool:
    try:
        register_checkout_plugin(_settings_path(), repo_dir)
    except (OSError, ValueError) as exc:
        output(f"Could not update Claude user settings: {exc}")
        return False
    output(
        f"Registered MEOW as a user-wide Claude plugin: {repo_dir}\n"
        "Restart Claude Code to load /meow:* skills (including /meow:migration)."
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
            "Set up MEOW in your projects now? You can also do it later with "
            "`claude /meow:onboard`. [Y/n]: "
        )
    except EOFError:
        output("")
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
            output("")
            return
        if not raw:
            return
        yield raw


def _onboard_one(project: Path, output: Callable[[str], None]) -> bool:
    """Onboard one project; True when it was set up (not skipped or failed)."""
    try:
        report = onboard_project(project)
    except (OSError, ValueError, RuntimeError) as exc:
        output(f"{project}: onboarding failed ({exc})")
        return False
    _print_report(project, report, output)
    if report.error:
        return False
    remember(project)
    return True


def _onboard_projects(
    ask: Callable[[str], str],
    output: Callable[[str], None],
    ask_path: Callable[[str], str] | None = None,
    onboarded: list[Path] | None = None,
) -> None:
    """Onboard the projects the user enters, recording each success in ``onboarded``."""
    done = onboarded if onboarded is not None else []
    if not _wants_projects_now(ask, output):
        return
    seen: set[Path] = set()
    for raw in _project_patterns(ask_path or ask, output):
        for project in expand_project_pattern(raw, ask=ask, output=output):
            if project in seen:
                continue
            seen.add(project)
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


def _setup_projects() -> int:
    """Run the project prompts; Ctrl+C cancels the whole installer."""
    onboarded: list[Path] = []
    try:
        _onboard_projects(input, print, path_prompt(), onboarded)
    except KeyboardInterrupt:
        print(NEWLINE + _cancelled_message(len(onboarded)))
        return EXIT_INTERRUPTED
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the interactive portion shared by the PowerShell and POSIX scripts."""
    parser = _parser()
    args = parser.parse_args(argv)
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
    return _setup_projects()
