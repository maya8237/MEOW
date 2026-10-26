"""Published-release discovery and updates; never update from a branch tip."""

import json
import re
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, distribution, version
from pathlib import Path
from urllib.request import urlopen

RELEASE_URL = (
    "https://raw.githubusercontent.com/maya8237/MEOW/release-channel/latest.json"
)
VERSION_COMPONENTS = 3
ANNOUNCE_LIMIT = 5


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


def update() -> int:
    from meow.installer.releases import install_release

    current = version("meow-harness")
    latest, digest = latest_release()
    if _version_parts(current) >= _version_parts(latest):
        print(f"MEOW {current} is up to date (latest release: {latest}).")
        return 0
    print(f"Upgrading MEOW {current} to release {latest}...")
    release = install_release(latest, digest)
    print(f"MEOW updated successfully to {latest}.")
    announce_onboarding(current, latest, release)
    offer_bun_store(release)
    return 0


def _local_install() -> bool:
    """Editable or checkout installs may report a stale package version."""
    try:
        text = distribution("meow-harness").read_text("direct_url.json")
    except PackageNotFoundError:
        return True
    return bool(text) and "dir_info" in json.loads(text)


def _new_options(old: str, new: str, release: Path) -> list[str]:
    from meow.installer.releases import release_python

    # This process still runs the old release; only the new one knows its table.
    result = subprocess.run(
        [str(release_python(release)), "-m", "meow.project.announcements"],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    low, high = _version_parts(old), _version_parts(new)
    summaries = []
    for entry in json.loads(result.stdout).values():
        try:
            added = _version_parts(entry["since"])
        except ValueError:
            continue
        if low < added <= high:
            summaries.append((added, entry["summary"]))
    return [summary for _, summary in sorted(summaries, key=lambda item: item[0])]


def announce_onboarding(old: str, new: str, release: Path) -> None:
    """Best-effort: list onboarding options added after OLD up to NEW."""
    try:
        if _local_install():
            return
        options = _new_options(old, new, release)
    except Exception:  # never fail an update that already installed
        return
    if not options:
        return
    lines = [f"New onboarding options since MEOW {old}:"]
    lines += [f"  - {option}" for option in options[:ANNOUNCE_LIMIT]]
    if len(options) > ANNOUNCE_LIMIT:
        lines.append(f"  ... and {len(options) - ANNOUNCE_LIMIT} more")
    lines.append("Run /meow:onboard in each project to review them.")
    print("\n".join(lines))


def offer_bun_store(release: Path) -> None:
    """Best-effort, interactive only: the new release offers Bun's shared store once."""
    from meow.installer.releases import release_python

    try:
        if _local_install() or not sys.stdin.isatty():
            return
        subprocess.run(
            [str(release_python(release)), "-m", "meow.installer", "--update-bun"],
            check=False,
        )
    except Exception:  # never fail an update that already installed
        return
