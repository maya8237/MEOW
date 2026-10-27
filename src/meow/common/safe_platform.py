"""Keep ``platform`` queries off WMI.

CPython 3.12+ asks WMI (``_wmi.exec_query``) for the OS version and CPU on
Windows. A wedged WMI service makes that call block forever, and
``platform.system()`` runs inside client User-Agent builders, the Claude SDK
transport and logging, so meow would hang before doing any work.
`install_safe_platform` answers those queries from ``sys.getwindowsversion``
and the environment instead, and makes ``platform._wmi_query`` fail at once,
which CPython already treats as "WMI unavailable".
"""

from __future__ import annotations

import os
import platform
import sys
import threading
from collections.abc import Callable

_VERSION_PARTS = 3
_MARKER = "_meow_safe_platform"
FORCE_WIN32_ENV = "MEOW_TEST_FORCE_WIN32"

WMI_DEPENDENT_COMMANDS = frozenset({
    "wmic",
    "Get-WmiObject",
    "Get-CimInstance",
    "systeminfo",
    "gwmi",
    "gcim",
})


def _wmi_unavailable(*_args: object, **_kwargs: object) -> None:
    raise OSError("WMI is not queried by meow")


def _windows_version() -> tuple[str, str, str, bool]:
    """Return (release, version, csd, is_client) without WMI or `ver`."""
    getter = getattr(sys, "getwindowsversion", None)
    winver = getter() if getter else None
    parts = (getattr(winver, "platform_version", None) or tuple(winver or ())[:3])[:3]
    version = (
        ".".join(str(part) for part in parts) if len(parts) == _VERSION_PARTS else ""
    )
    is_client = getattr(winver, "product_type", 1) == 1
    spack = getattr(winver, "service_pack_major", None)
    csd = f"SP{spack}" if spack is not None else ""
    release = ""
    if version:
        tables = (
            getattr(platform, "_WIN32_CLIENT_RELEASES", ())
            if is_client
            else getattr(platform, "_WIN32_SERVER_RELEASES", ())
        )
        number = tuple(int(part) for part in version.split("."))
        release = next((name for floor, name in tables if floor <= number), "")
    return release, version, csd, is_client


def _machine() -> str:
    return os.environ.get("PROCESSOR_ARCHITEW6432", "") or os.environ.get(
        "PROCESSOR_ARCHITECTURE", ""
    )


def _safe_win32_ver(
    release: str = "", version: str = "", csd: str = "", ptype: str = ""
) -> tuple[str, str, str, str]:
    found_release, found_version, found_csd, _ = _windows_version()
    return (
        found_release or release,
        found_version or version,
        found_csd or csd,
        ptype,
    )


def install_safe_platform() -> None:
    """Pre-fill ``platform``'s caches on Windows; a no-op elsewhere."""
    if sys.platform != "win32" and not os.environ.get(FORCE_WIN32_ENV):
        return
    if getattr(platform, _MARKER, False):
        return
    platform._wmi_query = _wmi_unavailable
    platform._wmi = None
    platform.win32_ver = _safe_win32_ver
    release, version, _, _ = _windows_version()
    platform._uname_cache = platform.uname_result(
        "Windows",
        os.environ.get("COMPUTERNAME", ""),
        release,
        version or "32bit",
        _machine(),
    )
    platform._platform_cache.clear()
    setattr(platform, _MARKER, True)


def bounded_platform_call[T](
    fn: Callable[[], T], timeout: float = 5.0, default: T | None = None
) -> T | None:
    """Run `fn` in a daemon thread; return `default` if it outlasts `timeout`."""
    outcome: list[T] = []

    def target() -> None:
        try:
            outcome.append(fn())
        except Exception:
            return

    worker = threading.Thread(target=target, daemon=True, name="meow-platform-query")
    worker.start()
    worker.join(timeout)
    return outcome[0] if outcome else default
