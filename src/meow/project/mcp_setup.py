"""User-scope MCP setup: register supported servers through `claude mcp add`.

MEOW never installs a launcher and never writes `~/.claude.json` itself.
Credentials come from the servers' own environment variable names (or a hidden
prompt) and appear nowhere in results, logs, or MEOW config.
"""

import getpass
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from meow.project.mcp_registry import _provider_matches, user_scope_servers

_SECRET_MARKERS = ("TOKEN", "PASSWORD", "SECRET")
_GITHUB_IMAGE = "ghcr.io/github/github-mcp-server"

REGISTERED = "registered"
ALREADY = "already_registered"
FAILURES = ("no_claude", "no_launcher", "no_credentials", "failed", "unsupported")

Prompt = Callable[[str, bool], str]


@dataclass(frozen=True)
class McpSpec:
    provider: str
    label: str
    server: str  # name registered with Claude
    launcher: str  # executable that must already be installed
    args: tuple[str, ...]
    auth: tuple[tuple[str, ...], ...]  # alternative sets of required variables
    optional: tuple[str, ...] = ()
    docker: bool = False  # pass each variable through with `docker run -e`
    warmup: tuple[str, ...] = ()  # fetch the package once, before Claude's timeout


SPECS = {
    "jira": McpSpec(
        "jira",
        "Jira",
        "atlassian",
        "uvx",
        ("mcp-atlassian",),
        auth=(
            ("JIRA_URL", "JIRA_USERNAME", "JIRA_API_TOKEN"),
            ("JIRA_URL", "JIRA_PERSONAL_TOKEN"),
        ),
        warmup=("uvx", "mcp-atlassian", "--help"),
    ),
    "github": McpSpec(
        "github",
        "GitHub",
        "github",
        "docker",
        ("run", "-i", "--rm"),
        auth=(("GITHUB_PERSONAL_ACCESS_TOKEN",),),
        optional=("GITHUB_HOST",),
        docker=True,
        warmup=("docker", "pull", _GITHUB_IMAGE),
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


def parse_providers(raw: str) -> tuple[str, ...]:
    """`jira,github` (or `all`) to provider names; unknown names are kept."""
    names = [part.strip().lower() for part in raw.split(",") if part.strip()]
    return SUPPORTED if names == ["all"] else tuple(dict.fromkeys(names))


def _is_secret(name: str) -> bool:
    return any(marker in name for marker in _SECRET_MARKERS)


def _registered_server(provider: str, home: Path | None) -> str | None:
    names = _provider_matches(user_scope_servers(home=home))[provider]
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
    names = [*alternative, *(n for n in spec.optional if environ.get(n))]
    return {name: environ[name] for name in names}


def build_command(spec: McpSpec, values: Mapping[str, str]) -> list[str]:
    """The `claude mcp add` argv; contains credentials, so never print it."""
    launch = [spec.launcher, *spec.args]
    if spec.docker:
        launch += [arg for name in values for arg in ("-e", name)] + [_GITHUB_IMAGE]
    env = [arg for name, value in values.items() for arg in ("-e", f"{name}={value}")]
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
            timeout=300,
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
    existing = _registered_server(spec.provider, home)
    if existing:
        detail = f"user-scope server '{existing}' already registered; left unchanged"
        return SetupResult(spec.provider, ALREADY, detail)
    if which("claude") is None:
        return SetupResult(spec.provider, "no_claude", "`claude` CLI is not installed")
    if which(spec.launcher) is None:
        detail = f"`{spec.launcher}` is not installed; MEOW does not install it"
        return SetupResult(spec.provider, "no_launcher", detail)
    return None


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
    home: Path | None = None,
    run: Callable = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> SetupResult:
    """Register one provider at user scope unless it is already registered."""
    blocked = _blocked(spec, home, which)
    if blocked:
        return blocked
    values = _collect(spec, environ, prompt)
    if values is None:
        return SetupResult(spec.provider, "no_credentials", _missing_message(spec))
    failure = _add(spec, values, run)
    if failure:
        return failure
    _warm_up(spec, run)
    detail = f"registered '{spec.server}' at user scope; run /mcp in Claude to check it"
    return SetupResult(spec.provider, REGISTERED, detail)


def setup_providers(  # ruff: ignore[too-many-arguments]
    providers: Sequence[str],
    environ: Mapping[str, str],
    *,
    prompt: Prompt | None = None,
    home: Path | None = None,
    run: Callable = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> list[SetupResult]:
    results = []
    for provider in providers:
        spec = SPECS.get(provider)
        if spec is None:
            detail = f"no supported MCP setup (supported: {', '.join(SUPPORTED)})"
            results.append(SetupResult(provider, "unsupported", detail))
            continue
        results.append(
            setup_provider(
                spec, environ, prompt=prompt, home=home, run=run, which=which
            )
        )
    return results


def describe(result: SetupResult) -> str:
    spec = SPECS.get(result.provider)
    return f"{spec.label if spec else result.provider}: {result.detail}"


def hidden_prompt(label: str, secret: bool) -> str:
    """Ask on the terminal; secrets are not echoed."""
    return getpass.getpass(f"{label}: ") if secret else input(f"{label}: ")
