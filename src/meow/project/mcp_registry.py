"""Local Claude MCP discovery and lazy stdio references. Never writes registrations."""

import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from meow.infrastructure.logging import get_logger

logger = get_logger(__name__)

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


def _user_path(home: Path | None) -> Path:
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR") if home is None else None
    if config_dir:
        return Path(config_dir) / ".claude.json"
    return (home or Path.home()) / ".claude.json"


def user_scope_servers(*, home: Path | None = None) -> dict:
    """Servers registered at Claude's user scope; may contain credentials."""
    return _servers(_read(_user_path(home)))


def _servers(source: dict) -> dict:
    servers = source.get("mcpServers", {})
    return servers if isinstance(servers, dict) else {}


def _rejected_project_servers(root: Path, claude_dir: Path) -> set[str]:
    """Names Claude's settings reject for `.mcp.json`; any settings file wins."""
    names: set[str] = set()
    for path in (
        claude_dir / "settings.json",
        root / ".claude/settings.json",
        root / ".claude/settings.local.json",
    ):
        rejected = _read(path).get("disabledMcpjsonServers", [])
        if isinstance(rejected, list):
            names.update(name for name in rejected if isinstance(name, str))
    return names


def discover_registry(root: Path, *, home: Path | None = None) -> dict:
    """Effective registrations: user global < project .mcp.json < user project.

    Values may contain credentials; callers must never serialize this mapping.
    Invalid higher-priority entries shadow lower-priority entries too.
    """
    root = Path(root).resolve()
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR") if home is None else None
    user = _read(_user_path(home))
    scoped = _project_scope(user, root)
    project = dict(_servers(_read(root / ".mcp.json")))
    claude_dir = Path(config_dir) if config_dir else (home or Path.home()) / ".claude"
    for name in _rejected_project_servers(root, claude_dir):
        project.pop(name, None)
    result = {}
    for servers in (_servers(user), project, _servers(scoped)):
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


def _user_scope_fallback(provider: str, hint: str) -> dict:
    """The one user-scope server for `provider`; project config names none."""
    servers = user_scope_servers()
    names = _provider_matches(servers)[provider]
    if len(names) > 1:
        raise ValueError(
            f"Several user-scope MCP servers match {provider} ({', '.join(names)}). "
            f'Set [{provider}.mcp] from = "NAME" in .meow/config.local.toml.'
        )
    if not names:
        raise ValueError(f"No {provider} MCP server is registered. {hint}")
    try:
        return _stdio(servers[names[0]])
    except ValueError as exc:
        raise ValueError(f"User-scope MCP server '{names[0]}': {exc}. {hint}") from None


_SITE_VARIABLES = {
    "jira": "JIRA_URL",
    "gitlab": "GITLAB_API_URL",
    "github": "GITHUB_HOST",
}


def configured_url(config: dict, provider: str) -> str | None:
    """The project's non-secret `[provider].url`, if it set one."""
    section = config.get(provider)
    url = section.get("url") if isinstance(section, dict) else None
    return url if isinstance(url, str) and url.strip() else None


def _host(url: str) -> str:
    return (urlparse(url if "://" in url else f"//{url}").hostname or "").lower()


def _warn_site_mismatch(config: dict, provider: str, mcp: dict) -> None:
    """Warn when the registered server targets another site than the project."""
    project = configured_url(config, provider)
    server = mcp.get("env", {}).get(_SITE_VARIABLES[provider])
    if project and isinstance(server, str) and _host(project) != _host(server):
        logger.warning(
            "mcp_site_mismatch",
            provider=provider,
            project_host=_host(project),
            server_host=_host(server),
        )


def provider_mcp_config(config: dict, provider: str) -> dict:
    """Resolve the requested provider and warn about a site mismatch."""
    mcp = _resolve_provider(config, provider)
    _warn_site_mismatch(config, provider, mcp)
    return mcp


def _resolve_provider(config: dict, provider: str) -> dict:
    """Resolve only the requested provider; loaded config remembers launch root.

    Without an `[x.mcp]` table the user's single matching user-scope server is
    used, so shared project config need not describe how any one user runs it.
    """
    hint = (
        f"Run `meow onboard --mcp {provider}` to register one for your user, or "
        f'set [{provider}.mcp] from = "NAME" to use an existing registration. '
        "An inline stdio command is also supported."
    )
    section = config.get(provider)
    if not isinstance(section, dict) or "mcp" not in section:
        return _user_scope_fallback(provider, hint)
    try:
        return resolve_mcp(
            section["mcp"], Path(config.get("_project_root", Path.cwd()))
        )
    except ValueError as exc:
        raise ValueError(f"[{provider}.mcp]: {exc}. {hint}") from None


def provider_environment(
    config: dict, provider: str, *, resolve_reference: bool = True
) -> dict:
    """Read inline API settings or a referenced provider's runtime environment."""
    section = config.get(provider)
    mcp = section.get("mcp") if isinstance(section, dict) else None
    if mcp is not None and not isinstance(mcp, dict):
        return {}
    if mcp is None or "from" in mcp:
        if not resolve_reference:
            return {}
        try:
            mcp = provider_mcp_config(config, provider)
        except ValueError:
            if mcp is None:
                return {}
            raise
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
    references, hints, launchers = {}, {}, {}
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
                launchers[provider] = Path(registry[names[0]]["command"]).name
    return {"references": references, "hints": hints, "launchers": launchers}
