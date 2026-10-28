"""Local Claude MCP discovery and lazy stdio references. Never writes registrations."""

import json
import os
import re
from pathlib import Path

_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
PROVIDERS = ("jira", "github", "gitlab")
_MAX_REGISTRY_CHARS = 2_000_000


def _read(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8-sig") as stream:
            content = stream.read(_MAX_REGISTRY_CHARS + 1)
        if len(content) > _MAX_REGISTRY_CHARS:
            return {}
        data = json.loads(content)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _project_scope(user: dict, root: Path) -> dict:
    projects = user.get("projects", {})
    if isinstance(projects, dict):
        for key, value in projects.items():
            if isinstance(value, dict) and _same_root(key, root):
                return value
    return {}


def _same_root(value: str, root: Path) -> bool:
    try:
        return Path(value).resolve() == root
    except (OSError, ValueError):
        return False


def _disabled(source: dict) -> set[str]:
    names = set()
    for field in ("disabledMcpServers", "disabledMcpjsonServers"):
        disabled = source.get(field, [])
        if isinstance(disabled, list):
            names.update(name for name in disabled if isinstance(name, str))
    return names


def discover_registry(root: Path, *, home: Path | None = None) -> dict:
    """Effective registrations: user global < project .mcp.json < user project.

    Values may contain credentials; callers must never serialize this mapping.
    Invalid higher-priority entries shadow lower-priority entries too.
    """
    root = Path(root).resolve()
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR") if home is None else None
    user_path = (
        Path(config_dir) / ".claude.json"
        if config_dir
        else (home or Path.home()) / ".claude.json"
    )
    user = _read(user_path)
    scoped = _project_scope(user, root)
    result = {}
    for source in (user, _read(root / ".mcp.json"), scoped):
        servers = source.get("mcpServers", {})
        if isinstance(servers, dict):
            result.update(servers)
    for name in _disabled(user) | _disabled(scoped):
        result.pop(name, None)
    return result


def _expand(value: str) -> str:
    def replace(match):
        name, default = match.groups()
        if name in os.environ:
            return os.environ[name]
        if default is not None:
            return default
        raise ValueError("MCP registration needs an environment variable")

    return _VARIABLE.sub(replace, value)


def _stdio(server: object, *, expand: bool = True) -> dict:
    if not isinstance(server, dict):
        raise ValueError("MCP registration must be a stdio server table")
    if (
        server.get("disabled")
        or server.get("type", "stdio") != "stdio"
        or any(key in server for key in ("url", "oauth", "headers"))
    ):
        raise ValueError(
            "MCP registration is disabled or uses unsupported HTTP/OAuth; "
            "configure stdio"
        )
    command, args, env = (
        server.get("command"),
        server.get("args", []),
        server.get("env", {}),
    )
    valid_command = isinstance(command, str) and bool(command.strip())
    valid_args = isinstance(args, list) and all(isinstance(arg, str) for arg in args)
    valid_env = isinstance(env, dict) and all(
        isinstance(k, str) and isinstance(v, str) for k, v in env.items()
    )
    if not all((valid_command, valid_args, valid_env)):
        raise ValueError(
            "MCP stdio registration needs command, string args and string env"
        )
    expand_value = _expand if expand else str
    result = {
        "command": expand_value(command),
        "args": [expand_value(arg) for arg in args],
    }
    if not result["command"].strip():
        raise ValueError("MCP registration needs a nonempty command")
    if "env" in server:
        result["env"] = {key: expand_value(value) for key, value in env.items()}
    return result


def resolve_mcp(mcp: object, root: Path) -> dict:
    """Validate inline stdio or resolve `{'from': name}` against launch root now."""
    if isinstance(mcp, dict) and "from" in mcp:
        if set(mcp) != {"from"} or not isinstance(mcp["from"], str) or not mcp["from"]:
            raise ValueError("MCP reference must contain only a nonempty 'from' name")
        registry = discover_registry(root)
        if mcp["from"] not in registry:
            raise ValueError("MCP reference not found; check Claude MCP registrations")
        return _stdio(registry[mcp["from"]])
    return _stdio(mcp)


def provider_mcp_config(config: dict, provider: str) -> dict:
    """Resolve only the requested provider; loaded config remembers launch root."""
    hint = (
        f'Configure [{provider}.mcp] from = "registered-name" using an existing '
        "Claude MCP registration; select one explicitly if matches are ambiguous. "
        "An inline stdio command is also supported."
    )
    section = config.get(provider)
    if not isinstance(section, dict):
        raise ValueError(f"No [{provider}] table found in .meow/config.toml. {hint}")
    try:
        return resolve_mcp(
            section.get("mcp"), Path(config.get("_project_root", Path.cwd()))
        )
    except ValueError as exc:
        raise ValueError(f"[{provider}.mcp]: {exc}. {hint}") from None


def provider_environment(
    config: dict, provider: str, *, resolve_reference: bool = True
) -> dict:
    """Read inline API settings or a referenced provider's runtime environment."""
    section = config.get(provider)
    mcp = section.get("mcp") if isinstance(section, dict) else None
    if not isinstance(mcp, dict):
        return {}
    if "from" in mcp:
        if not resolve_reference:
            return {}
        mcp = provider_mcp_config(config, provider)
    env = mcp.get("env", {})
    return env if isinstance(env, dict) else {}


def _provider_matches(registry: dict) -> dict:
    matches = {provider: [] for provider in PROVIDERS}
    for name, server in registry.items():
        server = server if isinstance(server, dict) else {}
        signature = " ".join(
            str(value)
            for value in (
                name,
                server.get("command", ""),
                server.get("args", []),
                server.get("url", ""),
            )
        ).lower()
        for provider in PROVIDERS:
            if provider in signature or (
                provider == "jira" and "atlassian" in signature
            ):
                matches[provider].append(name)
    return matches


def discover_provider_references(root: Path, *, home: Path | None = None) -> dict:
    """Credential-free discovery for setup: references plus safe ambiguous hints.

    Exactly one matching, supported registration yields `references[provider]`.
    Matching remote/invalid registrations count toward ambiguity, never fall back.
    """
    registry = discover_registry(root, home=home)
    matches = _provider_matches(registry)
    references, hints = {}, {}
    for provider, names in matches.items():
        if len(names) > 1:
            hints[provider] = (
                "Multiple Claude MCP registrations match; select one explicitly"
            )
        elif names:
            try:
                _stdio(registry[names[0]], expand=False)
            except ValueError:
                hints[provider] = (
                    "Matching Claude MCP registration needs valid stdio configuration"
                )
            else:
                references[provider] = {"from": names[0]}
    return {"references": references, "hints": hints}
