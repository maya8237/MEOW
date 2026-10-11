"""Small role-scoped permission policy enforced at the SDK tool boundary."""

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

_ACTIONS = frozenset({"allow", "deny", "ask"})
# Tool-input keys that name the file a tool call targets.
PATH_KEYS = ("file_path", "path", "notebook_path")
_ROLES = frozenset({
    "planner",
    "generator",
    "reviewer",
    "tester",
    "review_fixer",
    "lint_fixer",
    "issue_fetcher",
    "gitlab_fetcher",
    "github_fetcher",
    "docs_updater",
    "setup",
    "explorer",
})


def _relative_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or ".." in path.parts
        or (len(normalized) > 1 and normalized[1] == ":")
    ):
        raise ValueError("permission path must be relative to the project")
    return "/".join(path.parts).casefold()


_FILE_TOOL_FAMILY = {
    "Read": "Read",
    "Grep": "Read",
    "Glob": "Read",
    "Write": "Write",
    "Edit": "Write",
    "NotebookEdit": "Write",
}
_SEVERITY = {"allow": 0, "ask": 1, "deny": 2}


def _family(tool: str) -> str:
    return _FILE_TOOL_FAMILY.get(tool, tool)


def _contains(outer: str, inner: str) -> bool:
    """True when ``inner`` is ``outer`` or below it; "" is the project root."""
    return not outer or inner == outer or inner.startswith(f"{outer}/")


def _strictest(actions: list[str]) -> str:
    return max(actions, key=_SEVERITY.__getitem__)


@dataclass(frozen=True)
class Rule:
    role: str
    tool: str
    action: str
    path: str | None = None


@dataclass(frozen=True)
class RolePolicy:
    rules: tuple[Rule, ...]

    def gated_tools(self) -> set[str]:
        """Every tool whose calls this policy must see.

        A rule on one file tool covers its whole family (Edit is a Write, Grep
        and Glob read), and path rules also gate Bash and Agent.
        """
        families = {_family(rule.tool) for rule in self.rules}
        gated = {tool for tool in _FILE_TOOL_FAMILY if _family(tool) in families}
        gated |= {rule.tool for rule in self.rules}
        if any(rule.path is not None for rule in self.rules):
            gated |= {"Bash", "Agent"}
        return gated

    def decision(  # ruff: ignore[complex-structure, too-many-return-statements, too-many-branches]
        self, tool: str, tool_input: dict[str, Any], *, project_root: Path | None = None
    ) -> str | None:
        if tool in {"Bash", "Agent"} and any(
            rule.path is not None for rule in self.rules
        ):
            return "deny"
        matching = [rule for rule in self.rules if _family(rule.tool) == _family(tool)]
        if not matching:
            return None
        path_rules = [rule for rule in matching if rule.path is not None]
        if path_rules:
            raw_path = next(
                (tool_input[key] for key in PATH_KEYS if key in tool_input), None
            )
            if not isinstance(raw_path, str):
                return "deny"
            try:
                if project_root is not None:
                    candidate = Path(raw_path)
                    if not candidate.is_absolute():
                        candidate = project_root / candidate
                    raw_path = str(
                        candidate.resolve().relative_to(project_root.resolve())
                    )
                path = _relative_path(raw_path)
            except ValueError:
                return "deny"
            # A directory search also reaches protected paths beneath it.
            hits = [rule.action for rule in path_rules if _contains(rule.path, path)]
            hits += [
                rule.action
                for rule in path_rules
                if rule.action != "allow" and _contains(path, rule.path)
            ]
            if hits:
                return _strictest(hits)
        global_actions = [rule.action for rule in matching if rule.path is None]
        return _strictest(global_actions) if global_actions else None


@dataclass(frozen=True)
class PermissionPolicy:
    rules: tuple[Rule, ...] = ()

    def for_role(self, role: str) -> RolePolicy:
        return RolePolicy(tuple(rule for rule in self.rules if rule.role == role))


def parse_policy(  # ruff: ignore[complex-structure, too-many-branches, too-many-statements]
    raw: object,
    *,
    roles: set[str] | frozenset[str] = frozenset(),
) -> PermissionPolicy:
    if raw is None:
        return PermissionPolicy()
    if not isinstance(raw, dict) or set(raw) != {"rule"}:
        raise ValueError("[permissions] must contain only [[permissions.rule]]")
    entries = raw["rule"]
    if not isinstance(entries, list):
        raise ValueError("[[permissions.rule]] must be an array of tables")
    rules = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - {
            "role",
            "tool",
            "action",
            "path",
        }:
            raise ValueError("permission rule has unsupported fields")
        role, tool, action = (entry.get(key) for key in ("role", "tool", "action"))
        if not all(isinstance(v, str) and v.strip() for v in (role, tool, action)):
            raise ValueError("permission rule requires role, tool, and action")
        if role not in _ROLES | roles:
            raise ValueError(f"unsupported permission role: {role}")
        if action not in _ACTIONS:
            raise ValueError(f"unsupported permission action: {action}")
        if "path" in entry and tool not in {"Read", "Write", "Edit", "NotebookEdit"}:
            raise ValueError(f"path restrictions are unsupported for {tool}")
        path = _relative_path(entry["path"]) if "path" in entry else None
        identity = (role, tool, path)
        if identity in seen:
            raise ValueError("duplicate permission rule")
        seen.add(identity)
        rules.append(Rule(role, tool, action, path))
    for role in {rule.role for rule in rules if rule.path is not None}:
        for rule in rules:
            if (
                rule.role == role
                and rule.tool in {"Bash", "Agent"}
                and rule.action != "deny"
            ):
                raise ValueError(
                    f"path-scoped rules for {role} require {rule.tool} denied"
                )
    return PermissionPolicy(tuple(rules))


def make_permission_callback(
    policy: RolePolicy, *, unattended: bool, project_root: Path | None = None
):
    """Return an SDK callback; the SDK remains the tool executor."""

    async def can_use_tool(  # ruff: ignore[unused-async]
        tool: str, tool_input: dict[str, Any], _context: object
    ):
        decision = policy.decision(tool, tool_input, project_root=project_root)
        if decision in {None, "allow"}:
            return PermissionResultAllow()
        reason = "Project policy denied this action"
        if decision == "ask":
            reason = "Project policy requires approval for this action"
            if unattended:
                reason += "; unattended run stopped"
        return PermissionResultDeny(message=reason, interrupt=decision == "ask")

    return can_use_tool
