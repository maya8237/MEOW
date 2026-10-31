"""Readable tester setup facts: the no-browser warning and inferred values."""

_SELECTION_KEYS = ("browser_channel", "browser_executable", "browser_playwright")
DEFAULT_CHANNEL = "chrome"
DEFAULT_ORIGIN = "default"
_NOTICE = (
    "[tester] no browser selected; using Google Chrome "
    '(browser_channel = "chrome"). Set browser_channel, browser_executable or '
    "browser_playwright = true to choose another."
)


def apply_browser_default(config: dict) -> None:
    """Select Chrome when a frontend is configured and no browser is chosen."""
    tester = config.get("tester", {})
    frontend = tester.get("base_url") or tester.get("dev_server")
    if frontend and not any(tester.get(key) for key in _SELECTION_KEYS):
        config["tester"] = {**tester, "browser_channel": DEFAULT_CHANNEL}
        origins = config.setdefault("_detected_origins", {})
        origins["tester.browser_channel"] = DEFAULT_ORIGIN


def browser_default_notice(config: dict) -> str | None:
    """The notice that Chrome was defaulted for a frontend tester, else None."""
    origins = config.get("_detected_origins", {})
    if origins.get("tester.browser_channel") == DEFAULT_ORIGIN:
        return _NOTICE
    return None


_SKIP_HOST_CHECK = "PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS"


def effective_browser_env(tester: dict) -> dict:
    """``browser_env`` plus the host-library check skip for a real installed browser.

    The check only covers Playwright's bundled browsers, so it is skipped for
    ``browser_channel`` and ``browser_executable``; configured values win.
    """
    defaults = {}
    if tester.get("browser_channel") or tester.get("browser_executable"):
        defaults[_SKIP_HOST_CHECK] = "true"
    return {**defaults, **tester.get("browser_env", {})}


def _shown(value: object) -> str:
    if isinstance(value, list):
        return ", ".join(_shown(item) for item in value)
    return str(getattr(value, "command", value))


def inferred_tester_lines(config: dict) -> list[str]:
    """``Tester <key>: <value> (<source>)`` for every value read from the project."""
    tester = config.get("tester", {})
    lines = []
    for origin_key, origin in config.get("_detected_origins", {}).items():
        table, _, key = origin_key.partition(".")
        if table == "tester" and key in tester and key != "tests":
            lines.append(
                f"Tester {key}: {_shown(tester[key])} (inferred from {origin})"
            )
    return lines
