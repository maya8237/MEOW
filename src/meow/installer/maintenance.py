"""Published-release discovery and upgrades; never update from a branch tip."""

import json
import sys
from importlib.metadata import version
from urllib.request import urlopen

RELEASE_URL = (
    "https://raw.githubusercontent.com/maya8237/MEOW/release-channel/latest.json"
)
VERSION_COMPONENTS = 3


def _version_parts(value: str) -> tuple[int, ...]:
    parts = value.split(".")
    if len(parts) != VERSION_COMPONENTS or any(
        not part.isascii() or not part.isdecimal() for part in parts
    ):
        raise ValueError(f"Expected a three-part MEOW release version, got {value!r}")
    return tuple(int(part) for part in parts)


def latest_version(*, timeout: float = 10) -> str:
    with urlopen(RELEASE_URL, timeout=timeout) as response:
        tag = json.load(response)["tag_name"]
    if not isinstance(tag, str) or not tag.startswith("v"):
        raise ValueError("Expected a published MEOW vX.Y.Z release tag")
    result = tag[1:]
    _version_parts(result)
    return result


def check_for_updates() -> None:
    """Best-effort public-command warning; keep stdout and helper JSON clean."""
    try:
        latest, current = latest_version(timeout=1), version("meow")
        newer = _version_parts(current) < _version_parts(latest)
    except (OSError, ValueError, RuntimeError, KeyError):
        return
    if newer:
        print(
            f"Warning: MEOW {latest} is available (current: {current}). "
            "Please update with `meow --update` (or `meow -u`).",
            file=sys.stderr,
        )


def update() -> int:
    from meow.installer.releases import install_release

    current, latest = version("meow"), latest_version()
    if _version_parts(current) >= _version_parts(latest):
        print(f"MEOW {current} is up to date (latest release: v{latest}).")
        return 0
    print(f"Upgrading MEOW {current} to release v{latest}...")
    install_release(f"v{latest}")
    print(f"MEOW upgraded successfully to v{latest}.")
    return 0
