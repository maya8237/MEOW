"""Published-release discovery and updates; never update from a branch tip."""

import json
import os
import re
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, distribution, version
from pathlib import Path
from urllib.request import Request, urlopen

PYPI_INDEX_URL = os.environ.get(
    "MEOW_PYPI_INDEX_URL", "https://pypi.org/simple/meow-harness/"
)
PYPI_INDEX_ACCEPT = "application/vnd.pypi.simple.v1+json"
PYPI_LEGACY_URL = "https://pypi.org/pypi/meow-harness/json"
RELEASE_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:b(\d+))?")
ANNOUNCE_LIMIT = 5


def _release_key(value: str) -> tuple[int, ...]:
    """Order stable `X.Y.Z` and beta `X.Y.ZbN` versions; a beta precedes its stable."""
    match = RELEASE_VERSION.fullmatch(value) if value.isascii() else None
    if not match:
        raise ValueError(f"Expected a MEOW X.Y.Z or X.Y.ZbN version, got {value!r}")
    *base, beta = match.groups()
    return (*map(int, base), 0, int(beta)) if beta else (*map(int, base), 1, 0)


def _version_parts(value: str) -> tuple[int, ...]:
    """The stable `X.Y.Z` triple; announcements are only ever stamped with these."""
    key = _release_key(value)
    if not key[3]:
        raise ValueError(f"Expected a three-part MEOW release version, got {value!r}")
    return key[:3]


def _is_beta(value: str) -> bool:
    return not _release_key(value)[3]


def _wheel_candidate(file: object) -> tuple[tuple[int, ...], str, str] | None:
    if not isinstance(file, dict) or file.get("yanked"):
        return None
    filename = file.get("filename")
    match = re.fullmatch(r"meow[-_]harness-([^-]+)-[^/]+\.whl", str(filename))
    hashes = file.get("hashes")
    digest = str(hashes.get("sha256")) if isinstance(hashes, dict) else ""
    if not match or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return None
    try:
        return _release_key(match.group(1)), match.group(1), digest
    except ValueError:
        return None


def _pypi_wheel_release(
    files: object, channel: str, pin: str | None
) -> tuple[tuple[int, ...], str, str]:
    """The (key, version, sha256) among wheel FILES that CHANNEL or PIN selects."""
    wanted = {"stable": (1,), "beta": (0,), "any": (0, 1)}[channel]
    candidates = [
        candidate
        for candidate in map(_wheel_candidate, files if isinstance(files, list) else [])
        if candidate and (candidate[1] == pin if pin else candidate[0][3] in wanted)
    ]
    if not candidates:
        what = f"MEOW {pin}" if pin else f"a {channel} MEOW"
        raise ValueError(f"PyPI has no wheel for {what} release")
    return max(candidates)


def latest_release(
    *, channel: str = "stable", pin: str | None = None, timeout: float = 10
) -> tuple[str, str]:
    """The wheel on PyPI and its SHA-256: PIN exactly, else the newest of CHANNEL.

    CHANNEL is "stable", "beta" (prereleases only), or "any" (newest of both).
    """
    if pin:
        _release_key(pin)
    try:
        request = Request(PYPI_INDEX_URL, headers={"Accept": PYPI_INDEX_ACCEPT})
        with urlopen(request, timeout=timeout) as response:
            files = json.load(response).get("files")
        _, tag, digest = _pypi_wheel_release(files, channel, pin)
        return tag, digest
    except (AttributeError, OSError, TypeError, ValueError, KeyError):
        if channel != "stable" or pin:
            raise  # the legacy JSON only describes the newest stable release
        return _latest_pypi_legacy_release(timeout=timeout)


def latest_version(*, channel: str = "stable", timeout: float = 10) -> str:
    return latest_release(channel=channel, timeout=timeout)[0]


def _latest_pypi_legacy_release(*, timeout: float) -> tuple[str, str]:
    with urlopen(PYPI_LEGACY_URL, timeout=timeout) as response:
        data = json.load(response)
    info = data.get("info")
    tag = info.get("version") if isinstance(info, dict) else None
    if not isinstance(tag, str) or _is_beta(tag):
        raise ValueError("PyPI legacy JSON has no stable project version")
    for file in data.get("urls", []) if isinstance(data, dict) else []:
        if not isinstance(file, dict) or file.get("packagetype") != "bdist_wheel":
            continue
        if file.get("yanked"):
            continue
        digests = file.get("digests")
        digest = digests.get("sha256") if isinstance(digests, dict) else None
        if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest):
            return tag, digest
    raise ValueError("PyPI legacy JSON has no stable wheel with a SHA-256 checksum")


def check_for_updates() -> None:
    """Best-effort public-command warning; keep stdout and helper JSON clean."""
    try:
        current = version("meow-harness")
        latest = latest_version(
            channel="any" if _is_beta(current) else "stable", timeout=1
        )
        newer = _release_key(current) < _release_key(latest)
    except (OSError, ValueError, RuntimeError, KeyError):
        return
    if newer:
        print(
            f"Warning: MEOW {latest} is available (current: {current}). "
            "Please update with `meow --update` (or `meow -u`).",
            file=sys.stderr,
        )


def update(
    *, latest_pypi: bool = False, beta: bool = False, pin: str | None = None
) -> int:
    """Install the newest stable release, or the newest of any/beta, or PIN."""
    from meow.installer.releases import install_release

    current = version("meow-harness")
    channel = "any" if latest_pypi else "beta" if beta else "stable"
    label = {"any": "beta or stable", "beta": "beta", "stable": "stable"}[channel]
    print(
        f"Checking PyPI for MEOW {pin}..."
        if pin
        else f"Checking PyPI for the newest {label} release...",
        flush=True,
    )
    latest, digest = latest_release(channel=channel, pin=pin)
    # An explicit version may also be a downgrade; searches never are.
    if (_release_key(current) == _release_key(latest)) or (
        not pin and _release_key(current) > _release_key(latest)
    ):
        print(f"MEOW {current} is up to date (requested release: {latest}).")
        return 0
    print(f"Installing MEOW {latest} (current: {current})...")
    release = install_release(latest, digest)
    print(f"MEOW updated successfully to {latest}.")
    announce_onboarding(current, latest, release)
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
    low, high = _release_key(old)[:3], _release_key(new)[:3]
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
