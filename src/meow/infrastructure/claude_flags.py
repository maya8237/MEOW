"""Claude Code command-line flags an autopilot relaunch should carry over.

A relaunch resumes a conversation with a new prompt, so the original prompt,
session and output arguments are dropped; everything else (permission mode,
model, MCP config, extra directories, ...) is kept. Which options take values
comes from `claude --help`, with a built-in table when it cannot be read.
"""

import re
import shlex
import shutil
import subprocess
import sys
from functools import cache
from pathlib import Path

# Options never copied: a relaunch chooses its own prompt, session and output.
# `--allow-dangerously-skip-permissions` is deprecated: only
# `--dangerously-skip-permissions` carries the bypass mode over.
DROPPED = frozenset({
    "-p",
    "--print",
    "-c",
    "--continue",
    "-r",
    "--resume",
    "--session-id",
    "--fork-session",
    "--bg",
    "--background",
    "--output-format",
    "--input-format",
    "--include-partial-messages",
    "--replay-user-messages",
    "--from-pr",
    "--teleport",
    "-w",
    "--worktree",
    "--allow-dangerously-skip-permissions",
})
PERMISSION_FLAGS = frozenset({"--dangerously-skip-permissions", "--permission-mode"})
# Value kinds: "one" takes a value, "maybe" an optional one, "many" every
# following non-option token.
_FALLBACK = {
    "--model": "one",
    "--fallback-model": "one",
    "--permission-mode": "one",
    "--settings": "one",
    "--setting-sources": "one",
    "--append-system-prompt": "one",
    "--system-prompt": "one",
    "--append-system-prompt-file": "one",
    "--system-prompt-file": "one",
    "--agent": "one",
    "--agents": "one",
    "--effort": "one",
    "-n": "one",
    "--name": "one",
    "--session-id": "one",
    "--output-format": "one",
    "--input-format": "one",
    "--add-dir": "many",
    "--mcp-config": "many",
    "--plugin-dir": "many",
    "--allowedTools": "many",
    "--allowed-tools": "many",
    "--disallowedTools": "many",
    "--disallowed-tools": "many",
    "-r": "maybe",
    "--resume": "maybe",
    "--from-pr": "maybe",
    "--teleport": "maybe",
    "-w": "maybe",
    "--worktree": "maybe",
}
_OPTION = re.compile(
    r"^\s{1,4}(?P<names>-[\w-]+(?:,\s*-[\w-]+)*)(?:\s+(?P<value><[^>]+>|\[[^\]]+\]))?"
)


def parse_help(text: str) -> dict[str, str]:
    """Option name -> value kind, read from `claude --help` output."""
    kinds: dict[str, str] = {}
    for line in text.splitlines():
        match = _OPTION.match(line)
        if not match:
            continue
        value = match.group("value") or ""
        kind = (
            "many"
            if "..." in value
            else "maybe"
            if value.startswith("[")
            else "one"
            if value
            else "none"
        )
        for name in re.split(r",\s*", match.group("names")):
            kinds[name] = kind
    return kinds


@cache
def _help_kinds(claude: str) -> dict[str, str]:
    try:
        result = subprocess.run(
            [claude, "--help"], capture_output=True, text=True, timeout=20, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    return parse_help(result.stdout)


def option_kinds() -> dict[str, str]:
    claude = shutil.which("claude")
    learned = _help_kinds(claude) if claude else {}
    return {**_FALLBACK, **learned}


def _values(args: list[str], index: int, kind: str) -> int:
    """How many tokens after `args[index]` belong to that option."""
    following = args[index + 1 :]
    if kind == "one":
        return 1 if following else 0
    if kind == "maybe":
        return 1 if following and not following[0].startswith("-") else 0
    if kind == "many":
        count = 0
        while count < len(following) and not following[count].startswith("-"):
            count += 1
        return count
    return 0


def filter_flags(args: list[str], kinds: dict[str, str] | None = None) -> list[str]:
    """Keep the options a relaunch should reuse; drop prompts and sessions."""
    kinds = option_kinds() if kinds is None else kinds
    kept: list[str] = []
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--":
            break  # the rest is a positional prompt
        if not token.startswith("-") or token == "-":
            index += 1  # a positional prompt
            continue
        name, has_value, _ = token.partition("=")
        count = 0 if has_value else _values(args, index, kinds.get(name, "none"))
        if name not in DROPPED:
            kept.extend(args[index : index + 1 + count])
        index += 1 + count
    return kept


def _cmdline(pid: int) -> list[str]:  # ruff: ignore[too-many-return-statements]
    if sys.platform.startswith("linux"):
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            return []
        return [part.decode(errors="replace") for part in raw.split(b"\0") if part]
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["ps", "-ww", "-o", "args=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        try:
            return shlex.split(result.stdout.strip())
        except ValueError:
            return result.stdout.split()
    return []  # Windows: rely on `[run] claude_args`


def _arguments(argv: list[str]) -> list[str]:
    """Drop the program (and a `node script.js` launcher) from a command line."""
    for index, part in enumerate(argv):
        name = Path(part).name.lower()
        if name.startswith("claude") or name.endswith((".js", ".mjs", ".cjs")):
            return argv[index + 1 :]
    return argv[1:]


def process_flags(pid: int | None) -> list[str]:
    """The reusable flags of a running Claude process ([] when unreadable)."""
    if not pid:
        return []
    return filter_flags(_arguments(_cmdline(pid)))


def merge_flags(
    configured: list[str], copied: list[str], permission_mode: str | None
) -> list[str]:
    """`[run] claude_args`, then the copied flags, then the hook's permission
    mode when neither names one (`plan` mode cannot work unattended)."""
    flags = [*configured, *copied]
    if (
        permission_mode
        and permission_mode not in {"default", "plan"}
        and not PERMISSION_FLAGS & {flag.partition("=")[0] for flag in flags}
    ):
        flags += ["--permission-mode", permission_mode]
    return flags
