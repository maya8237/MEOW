"""MEOW never merges a pull/merge request unless the user asked for it.

MEOW's own delivery only pushes its feature branch and opens a PR/MR; landing
it is the user's call. This module recognises the ways an agent could merge
anyway and refuses them:

- merge commands: `gh pr merge`, `glab mr merge`, `hub merge`, and API calls to
  a PR/MR `merge` endpoint or that turn on auto-merge;
- `git push` to the delivery target branch (or `main`/`master`), which lands
  code without any MR at all;
- MCP tools that merge or enable auto-merge.

It is enforced twice: around every SDK agent MEOW starts (`agents.base`), and
for native sessions through the run/autopilot skills' `PreToolUse` hook
(`meow native merge-guard`). A merge is allowed only when `[delivery]
allow_merge = true`, or, in a native session, when the user's latest typed
message in the session transcript asks for a merge (skill text, tool output
and subagent turns are not typed messages).
"""

import json
import os
import re
import shlex
from fnmatch import fnmatchcase

DENIED = (
    "MEOW never merges pull/merge requests or pushes to the target branch unless "
    "the user asks. Leave the PR/MR open and tell the user it is ready to merge. "
    "To allow it, the user asks for the merge in their own message, or sets "
    "[delivery] allow_merge = true."
)
ALWAYS_PROTECTED = frozenset({"main", "master"})
MERGE_TOOLS = (
    "*merge_pull_request*",
    "*merge_merge_request*",
    "*accept_merge_request*",
    "*auto_merge*",
    "*automerge*",
    "*merge_mr*",
    "*merge_pr",
)
_COMMANDS = re.compile(
    r"""(?ix)
    \b(?:gh\s+pr\s+merge|glab\s+mr\s+(?:merge|accept)|hub\s+merge)\b
    | /pulls/[^/\s'"]+/merge\b
    | /merge_requests/[^/\s'"]+/merge\b
    | merge_when_pipeline_succeeds
    | auto_?merge
    | enablePullRequestAutoMerge|mergePullRequest
    """
)
_ASKED = re.compile(
    r"""(?ix)
    \b(?:merge|land|squash)\b[^.?!\n]{0,40}
    \b(?:pr|mr|pull\s+request|merge\s+request|it|this|that|them|branch|changes)\b
    | \b(?:pr|mr|pull\s+request|merge\s+request)\b[^.?!\n]{0,40}\bmerge\b
    """
)


def _tokens(command: str) -> list[list[str]]:
    """Each simple command of a shell line, split into words (best effort)."""
    parts = re.split(r"&&|\|\||;|\||\n", command)
    words = []
    for part in parts:
        try:
            words.append(shlex.split(part))
        except ValueError:
            words.append(part.split())
    return words


def _push_target(words: list[str], protected: set[str]) -> str | None:
    """The protected branch a `git push` would update, if any."""
    if "git" not in words:
        return None
    rest = words[words.index("git") + 1 :]
    while rest and rest[0].startswith("-"):  # git -C dir push ...
        rest = rest[2:] if rest[0] in {"-C", "-c"} else rest[1:]
    if not rest or rest[0] != "push":
        return None
    positional = [word for word in rest[1:] if not word.startswith("-")]
    for refspec in positional[1:]:  # the first positional is the remote
        destination = refspec.lstrip("+").rsplit(":", 1)[-1]
        destination = destination.removeprefix("refs/heads/")
        if destination in protected:
            return destination
    return None


def bash_reason(command: str, protected: set[str]) -> str | None:
    if _COMMANDS.search(command):
        return DENIED
    for words in _tokens(command):
        if _push_target(words, protected):
            return DENIED
    return None


def is_merge_tool(tool: str) -> bool:
    name = tool.lower()
    return name.startswith("mcp__") and any(
        fnmatchcase(name, pattern) for pattern in MERGE_TOOLS
    )


def reason(tool: str, tool_input: dict, protected: set[str]) -> str | None:
    """Why this tool call is a merge MEOW must refuse; None when it is not."""
    if is_merge_tool(tool):
        return DENIED
    if tool == "Bash":
        command = tool_input.get("command")
        if isinstance(command, str):
            return bash_reason(command, protected)
    return None


def protected_branches(config: dict) -> set[str]:
    target = (config.get("delivery") or {}).get("target_branch")
    return set(ALWAYS_PROTECTED) | ({target} if isinstance(target, str) else set())


def merge_allowed(config: dict) -> bool:
    return (config.get("delivery") or {}).get("allow_merge") is True


def asked_to_merge(prompt: str) -> bool:
    """The user's own message asks MEOW to merge (not "fix merge conflicts")."""
    text = re.sub(r"(?i)merge\s+conflicts?", "", prompt)
    text = re.sub(
        r"(?i)(?:do\s+not|don'?t|never|no)\s+(?:\w+\s+){0,2}merg\w*", "", text
    )
    return bool(_ASKED.search(text))


def _typed_text(entry: dict) -> str | None:  # ruff: ignore[too-many-return-statements]
    """The text a person typed, for a transcript entry that is one."""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isSidechain"):
        return None
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        if any(
            isinstance(item, dict) and item.get("type") == "tool_result"
            for item in content
        ):
            return None
        return " ".join(
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    return None


def latest_user_message(transcript: str | None) -> str:
    """The user's most recent typed message in a Claude Code transcript."""
    if not transcript:
        return ""
    try:
        with open(transcript, "rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - 2_000_000))
            lines = stream.read().decode(errors="replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and (text := _typed_text(entry)) is not None:
            return text
    return ""
