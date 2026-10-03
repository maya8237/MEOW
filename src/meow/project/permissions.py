"""Small role-scoped permission policy enforced at the SDK tool boundary."""

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

_ACTIONS = frozenset({"allow", "deny", "ask"})
_PATH_KEYS = ("file_path", "path", "notebook_path")
_ROLES = frozenset({
    "planner", "generator", "reviewer", "tester", "review_fixer",
    "lint_fixer", "issue_fetcher", "gitlab_fetcher", "docs_updater", "setup",
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


@dataclass(frozen=True)
class Rule:
    role: str
    tool: str
    action: str
    path: str | None = None


@dataclass(frozen=True)
class RolePolicy:
    rules: tuple[Rule, ...]

    def decision(  # ruff: ignore[complex-structure, too-many-return-statements, too-many-branches]
        self, tool: str, tool_input: dict[str, Any], *, project_root: Path | None = None
    ) -> str | None:
        if tool in {"Bash", "Agent"} and any(
            rule.path is not None for rule in self.rules
        ):
            return "deny"
        matching = [rule for rule in self.rules if rule.tool == tool]
        if not matching:
            return None
        path_rules = [rule for rule in matching if rule.path is not None]
        if path_rules:
            raw_path = next(
                (tool_input[key] for key in _PATH_KEYS if key in tool_input), None
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
            for rule in path_rules:
                if path == rule.path or path.startswith(f"{rule.path}/"):
                    return rule.action
        global_rule = next((rule for rule in matching if rule.path is None), None)
        return global_rule.action if global_rule else None


@dataclass(frozen=True)
class PermissionPolicy:
    rules: tuple[Rule, ...] = ()

    def for_role(self, role: str) -> RolePolicy:
        return RolePolicy(tuple(rule for rule in self.rules if rule.role == role))


def parse_policy(  # ruff: ignore[complex-structure, too-many-branches, too-many-statements]
    raw: object,
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
        if role not in _ROLES:
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
