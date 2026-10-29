"""User-scope MCP setup: register supported servers through `claude mcp add`.

MEOW never installs a launcher and never writes `~/.claude.json` itself.
Credentials come from the servers' own environment variable names (or a hidden
prompt) and appear nowhere in results, logs, or MEOW config.
"""

import getpass
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from meow.project.mcp_registry import _provider_matches, user_scope_servers

_SECRET_MARKERS = ("TOKEN", "PASSWORD", "SECRET")
_GITLAB_PACKAGE = "@zereight/mcp-gitlab"
# The Atlassian server also serves Confluence; Jira or Confluence alone suffices.
_ATLASSIAN_OPTIONAL = (
    "JIRA_SSL_VERIFY",
    "CONFLUENCE_URL",
    "CONFLUENCE_USERNAME",
    "CONFLUENCE_API_TOKEN",
    "CONFLUENCE_PERSONAL_TOKEN",
    "CONFLUENCE_SSL_VERIFY",
)

REGISTERED = "registered"
ALREADY = "already_registered"
FAILURES = ("no_claude", "no_launcher", "no_credentials", "failed", "unsupported")

Prompt = Callable[[str, bool], str]
Permission = Callable[[str], bool]  # may MEOW pip-install this package?


@dataclass(frozen=True)
class McpSpec:
    provider: str
    label: str
    server: str  # name registered with Claude
    launcher: str  # executable that must already be installed
    args: tuple[str, ...]
    auth: tuple[tuple[str, ...], ...]  # alternative sets of required variables
    warmup: tuple[str, ...] = ()  # fetch the package once, before Claude's timeout
    env: tuple[tuple[str, str], ...] = ()  # fixed, non-secret server settings
    optional: tuple[str, ...] = ()  # forwarded from the environment when set
    custom: bool = False  # any field came from [mcp_setup] config
    package: str = ""  # PyPI package providing `launcher`, if pip can install it


SPECS = {
    "jira": McpSpec(
        "jira",
        "Jira",
        "atlassian",
        "mcp-atlassian",
        (),
        auth=(
            ("JIRA_URL", "JIRA_USERNAME", "JIRA_API_TOKEN"),
            ("JIRA_URL", "JIRA_PERSONAL_TOKEN"),
            ("CONFLUENCE_URL", "CONFLUENCE_USERNAME", "CONFLUENCE_API_TOKEN"),
            ("CONFLUENCE_URL", "CONFLUENCE_PERSONAL_TOKEN"),
        ),
        package="mcp-atlassian",
        optional=_ATLASSIAN_OPTIONAL,
    ),
    "gitlab": McpSpec(
        "gitlab",
        "GitLab",
        "gitlab",
        "npx",
        ("-y", _GITLAB_PACKAGE),
        auth=(("GITLAB_PERSONAL_ACCESS_TOKEN", "GITLAB_API_URL"),),
        warmup=("npx", "-y", _GITLAB_PACKAGE, "--help"),
    ),
}
SUPPORTED = tuple(SPECS)


@dataclass(frozen=True)
class SetupResult:
    provider: str
    status: str
    detail: str

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "status": self.status,
            "detail": self.detail,
        }

    @property
    def failed(self) -> bool:
        return self.status in FAILURES


def parse_providers(raw: str, known: Mapping[str, McpSpec] = SPECS) -> tuple[str, ...]:
    """`atlassian,gitlab` (or `all`) to provider names; old names still work."""
    aliases = {"atlassian": "jira"}
    names = [
        aliases.get(part.strip().lower(), part.strip().lower())
        for part in raw.split(",")
        if part.strip()
    ]
    return tuple(known) if names == ["all"] else tuple(dict.fromkeys(names))


def _is_secret(name: str) -> bool:
    return any(marker in name for marker in _SECRET_MARKERS)


def _registered_server(spec: McpSpec, home: Path | None) -> str | None:
    servers = user_scope_servers(home=home)
    if spec.server in servers:
        return spec.server
    names = _provider_matches(servers).get(spec.provider, [])
    return names[0] if names else None


def _satisfied(spec: McpSpec, environ: Mapping[str, str]) -> tuple[str, ...] | None:
    for alternative in spec.auth:
        if all(environ.get(name) for name in alternative):
            return alternative
    return None


def _collect(
    spec: McpSpec, environ: Mapping[str, str], prompt: Prompt | None
) -> dict[str, str] | None:
    alternative = _satisfied(spec, environ)
    if alternative is None and prompt is not None:
        values = dict(environ)
        for name in spec.auth[0]:
            if not values.get(name):
                values[name] = prompt(name, _is_secret(name)).strip()
        alternative = _satisfied(spec, values)
        environ = values
    if alternative is None:
        return None
    extra = (name for name in spec.optional if environ.get(name))
    return {name: environ[name] for name in (*alternative, *extra)}


def build_command(spec: McpSpec, values: Mapping[str, str]) -> list[str]:
    """The `claude mcp add` argv; contains credentials, so never print it."""
    launch = [spec.launcher, *spec.args]
    pairs = [*spec.env, *values.items()]
    env = [arg for name, value in pairs for arg in ("-e", f"{name}={value}")]
    return ["claude", "mcp", "add", "--scope", "user", spec.server, *env, "--", *launch]


def _warm_up(spec: McpSpec, run: Callable) -> None:
    """Download the server once so Claude's first launch does not time out."""
    if not spec.warmup:
        return
    try:
        run(
            list(spec.warmup),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return


def _missing_message(spec: McpSpec) -> str:
    options = " or ".join("+".join(alternative) for alternative in spec.auth)
    return f"set {options} in the environment, then rerun"


def _blocked(
    spec: McpSpec, home: Path | None, which: Callable[[str], str | None]
) -> SetupResult | None:
    """Why nothing should be registered, or None when setup may proceed."""
    existing = _registered_server(spec, home)
    if existing:
        detail = f"user-scope server '{existing}' already registered; left unchanged"
        return SetupResult(spec.provider, ALREADY, detail)
    if which("claude") is None:
        return SetupResult(spec.provider, "no_claude", "`claude` CLI is not installed")
    return None


def _tools_dir(home: Path | None) -> Path:
    """MEOW's own environment for pip-installed servers; stable across updates."""
    return (home or Path.home()) / ".meow" / "tools"


def _tools_launcher(spec: McpSpec, home: Path | None) -> Path:
    scripts, suffix = ("Scripts", ".exe") if os.name == "nt" else ("bin", "")
    return _tools_dir(home) / scripts / f"{spec.launcher}{suffix}"


def _pip_install(spec: McpSpec, home: Path | None, run: Callable) -> str | None:
    """Install the package into the tools environment; a problem, or None."""
    environment = _tools_dir(home)
    steps = (
        []
        if environment.is_dir()
        else [[sys.executable, "-m", "venv", str(environment)]]
    )
    python = _tools_launcher(replace(spec, launcher="python"), home)
    steps.append([str(python), "-m", "pip", "install", spec.package])
    for argv in steps:
        try:
            done = run(
                argv,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=600,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return f"`pip install {spec.package}` could not run"
        if done.returncode:
            return f"`pip install {spec.package}` failed (exit {done.returncode})"
    return None


def _missing(spec: McpSpec) -> SetupResult:
    detail = f"`{spec.launcher}` is not installed; MEOW does not install it"
    if spec.package:
        detail += (
            f" unless asked (`python -m pip install {spec.package}`, "
            "or rerun with --install-missing)"
        )
    return SetupResult(spec.provider, "no_launcher", detail)


def _resolve(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
    spec: McpSpec,
    home: Path | None,
    which: Callable[[str], str | None],
    allow: Permission | None,
    run: Callable,
) -> tuple[McpSpec, SetupResult | None]:
    """The spec pointing at an installed launcher, or why there is none."""
    if which(spec.launcher):
        return spec, None
    ours = _tools_launcher(spec, home)
    if spec.package and not ours.exists() and allow and allow(spec.package):
        problem = _pip_install(spec, home, run)
        if problem:
            return spec, SetupResult(spec.provider, "no_launcher", problem)
    if spec.package and ours.exists():
        return replace(spec, launcher=str(ours)), None
    return spec, _missing(spec)


def _add(spec: McpSpec, values: Mapping[str, str], run: Callable) -> SetupResult | None:
    """Run `claude mcp add`; a failure result, or None on success."""
    try:
        done = run(
            build_command(spec, values),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return SetupResult(spec.provider, "failed", "`claude mcp add` could not run")
    if done.returncode == 0:
        return None
    # Output is withheld: it may echo the command line.
    detail = f"`claude mcp add` exited {done.returncode}"
    return SetupResult(spec.provider, "failed", detail)


def setup_provider(  # ruff: ignore[too-many-arguments]
    spec: McpSpec,
    environ: Mapping[str, str],
    *,
    prompt: Prompt | None = None,
    allow_install: Permission | None = None,
    home: Path | None = None,
    run: Callable = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> SetupResult:
    """Register one provider at user scope unless it is already registered."""
    blocked = _blocked(spec, home, which)
    if not blocked:
        spec, blocked = _resolve(spec, home, which, allow_install, run)
    if blocked:
        return blocked
    values = _collect(spec, environ, prompt)
    if values is None:
        return SetupResult(spec.provider, "no_credentials", _missing_message(spec))
    failure = _add(spec, values, run)
    if failure:
        return failure
    _warm_up(spec, run)
    source = " using the configured command" if spec.custom else ""
    detail = f"registered '{spec.server}' at user scope{source}; run /mcp to check it"
    return SetupResult(spec.provider, REGISTERED, detail)


def setup_providers(  # ruff: ignore[too-many-arguments]
    providers: Sequence[str],
    environ: Mapping[str, str],
    *,
    specs: Mapping[str, McpSpec] = SPECS,
    prompt: Prompt | None = None,
    allow_install: Permission | None = None,
    home: Path | None = None,
    run: Callable = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> list[SetupResult]:
    results = []
    for provider in providers:
        spec = specs.get(provider)
        if spec is None:
            detail = f"no supported MCP setup (supported: {', '.join(specs)})"
            results.append(SetupResult(provider, "unsupported", detail))
            continue
        results.append(
            setup_provider(
                spec,
                environ,
                prompt=prompt,
                allow_install=allow_install,
                home=home,
                run=run,
                which=which,
            )
        )
    return results


def describe(result: SetupResult) -> str:
    spec = SPECS.get(result.provider)
    return f"{spec.label if spec else result.provider}: {result.detail}"


_EXAMPLES = {
    "JIRA_URL": "https://yourcompany.atlassian.net",
    "JIRA_USERNAME": "you@yourcompany.com",
    "CONFLUENCE_URL": "https://yourcompany.atlassian.net/wiki",
    "GITLAB_API_URL": "https://gitlab.com/api/v4",
}


def hidden_prompt(label: str, secret: bool) -> str:
    """Ask on the terminal; secrets are not echoed."""
    example = f" (e.g. {_EXAMPLES[label]})" if label in _EXAMPLES else ""
    question = f"{label}{example}: "
    return getpass.getpass(question) if secret else input(question)


def ask_to_install(package: str) -> bool:
    """Interactive permission to pip-install a server; the default is no."""
    answer = input(f"Install {package} with pip into ~/.meow/tools? [y/N] ")
    return answer.strip().lower() in {"y", "yes"}


def always_install(package: str) -> bool:
    return True
