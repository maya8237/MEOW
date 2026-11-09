"""Notice, and repair, Claude Code loading an older MEOW plugin than is installed.

Claude Code caches a plugin per version, so after MEOW updates it can keep
serving the old copy. At startup we compare the version it reports with the
installed release and refresh when they differ.
"""

from importlib.metadata import PackageNotFoundError, version

from meow.project.claude_settings import (
    installed_plugin_version,
    refresh_installed_plugin,
)

PLUGIN = "meow"
STARTUP_TIMEOUT = 8  # the startup hook is killed after 30 s


def _running() -> str | None:
    try:
        return version("meow-harness")
    except PackageNotFoundError:
        return None


def plugin_drift(*, fix: bool, timeout: float = STARTUP_TIMEOUT) -> list[str]:
    """Lines describing a stale Claude Code plugin; with `fix`, refresh it first.

    Empty when versions match or cannot be compared (no `claude` CLI, MEOW not
    installed as a package, plugin not registered).
    """
    wanted = _running()
    loaded = installed_plugin_version(PLUGIN, timeout)
    if not wanted or not loaded or loaded == wanted:
        return []
    stale = f"Claude Code has MEOW plugin {loaded} but {wanted} is installed"
    if not fix:
        return [f"{stale}; run: meow sync"]
    warning = refresh_installed_plugin(PLUGIN, timeout)
    if warning:
        return [
            f"{stale} and refreshing failed: {warning}. Run `/plugin marketplace "
            f"update {PLUGIN}` then `/plugin update {PLUGIN}@{PLUGIN}` in Claude Code"
        ]
    return [f"{stale}; refreshed it. Run /reload-plugins (or restart) to load {wanted}"]
