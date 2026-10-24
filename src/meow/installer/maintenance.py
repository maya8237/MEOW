"""Published-release discovery and updates; never update from a branch tip."""

import json
import re
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


def _manifest(timeout: float) -> dict:
    with urlopen(RELEASE_URL, timeout=timeout) as response:
        data = json.load(response)
    tag = data["tag_name"]
    if not isinstance(tag, str):
        raise ValueError("Expected a published MEOW X.Y.Z release tag")
    _version_parts(tag)
    return data


def latest_version(*, timeout: float = 10) -> str:
    return _manifest(timeout)["tag_name"]


def latest_release(*, timeout: float = 10) -> tuple[str, str]:
    """The newest published tag with the SHA-256 of its wheel on the index."""
    data = _manifest(timeout)
    digest = data.get("wheel_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("The published MEOW release has no wheel checksum")
    return data["tag_name"], digest


def check_for_updates() -> None:
    """Best-effort public-command warning; keep stdout and helper JSON clean."""
    try:
        latest, current = latest_version(timeout=1), version("meow-harness")
        newer = _version_parts(current) < _version_parts(latest)
    except (OSError, ValueError, RuntimeError, KeyError):
        return
    if newer:
        print(
            f"Warning: MEOW {latest} is available (current: {current}). "
            "Please update with `meow --update` (or `meow -u`).",
            file=sys.stderr,
        )


def new_features(current: str, latest: str) -> list[str]:
    """Onboarding features introduced after `current`, up to and including `latest`."""
    from meow.project.onboarding import _SINCE

    old, new = _version_parts(current), _version_parts(latest)
    return sorted(k for k, v in _SINCE.items() if old < _version_parts(v) <= new)


def update() -> int:
    from meow.installer.releases import install_release

    current = version("meow-harness")
    latest, digest = latest_release()
    if _version_parts(current) >= _version_parts(latest):
        print(f"MEOW {current} is up to date (latest release: {latest}).")
        return 0
    print(f"Upgrading MEOW {current} to release {latest}...")
    install_release(latest, digest)
    print(f"MEOW updated successfully to {latest}.")
    if features := new_features(current, latest):
        print(
            f"New onboarding options: {', '.join(features)}. "
            "Run /meow:onboard in each project to review them."
        )
    return 0
