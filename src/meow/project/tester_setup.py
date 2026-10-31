"""Readable tester setup facts: the no-browser warning and inferred values."""

_SELECTION_KEYS = ("browser_channel", "browser_executable", "browser_playwright")
_WARNING = (
    '[tester] no browser selected; UI changes will FAIL ("browser not configured"). '
    "Set browser_channel, browser_executable, or browser_playwright = true "
    "(the previous behavior)."
)


def browser_selection_warning(config: dict) -> str | None:
    """The warning for a frontend tester with no browser selected, else None."""
    tester = config.get("tester", {})
    frontend = tester.get("base_url") or tester.get("dev_server")
    if frontend and not any(tester.get(key) for key in _SELECTION_KEYS):
        return _WARNING
    return None


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
