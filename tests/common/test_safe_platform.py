"""`platform` queries must never reach WMI, even when WMI hangs."""

import os
import platform
import subprocess
import sys
import threading
import time

import pytest
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

from meow.common import safe_platform
from meow.common.safe_platform import bounded_platform_call, install_safe_platform

_FAST = 2
_PATCHED = ("_wmi_query", "_wmi", "win32_ver", "_uname_cache", "_platform_cache")


@pytest.fixture
def hung_wmi(monkeypatch):
    """Pretend to be Windows with a WMI that blocks until the test ends."""
    release = threading.Event()
    calls = []

    def hang(*_args):
        calls.append(_args)
        release.wait(30)
        raise OSError("released")

    for name in _PATCHED:
        monkeypatch.setattr(
            platform, name, getattr(platform, name, None), raising=False
        )
    monkeypatch.delattr(platform, safe_platform._MARKER, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(platform, "_wmi_query", hang)
    monkeypatch.setattr(platform, "_uname_cache", None)
    monkeypatch.setattr(platform, "_platform_cache", {})
    yield calls
    release.set()


def _timed(fn):
    start = time.monotonic()
    fn()
    return time.monotonic() - start


def test_platform_calls_do_not_reach_wmi(hung_wmi):
    install_safe_platform()
    for query in (
        platform.system,
        platform.uname,
        platform.release,
        platform.version,
        platform.machine,
        platform.platform,
        platform.win32_ver,
    ):
        assert _timed(query) < _FAST, query.__name__
    assert platform.system() == "Windows"
    assert hung_wmi == []


def test_repro_with_windows_uname_path(hung_wmi, monkeypatch):
    # Real Windows has no os.uname, so uname() would use win32_ver/_wmi_query.
    monkeypatch.delattr(os, "uname", raising=False)
    install_safe_platform()
    for query in (platform.uname, platform.platform, platform.processor):
        assert _timed(query) < _FAST
    assert hung_wmi == []


def test_private_platform_attributes_exist_on_this_python():
    for name in safe_platform._PRIVATE_NAMES:
        if name == "_wmi" and sys.platform != "win32":
            continue  # only imported on Windows
        assert hasattr(platform, name), name


@pytest.mark.parametrize("missing", safe_platform._PRIVATE_NAMES)
def test_install_degrades_when_a_private_name_is_gone(hung_wmi, monkeypatch, missing):
    monkeypatch.delattr(platform, missing, raising=False)
    install_safe_platform()  # must not raise
    assert platform.win32_ver()[1] != "hang"


def test_force_flag_is_ignored_outside_pytest(monkeypatch):
    for name in _PATCHED:
        monkeypatch.setattr(
            platform, name, getattr(platform, name, None), raising=False
        )
    monkeypatch.delattr(platform, safe_platform._MARKER, raising=False)
    monkeypatch.setenv(safe_platform.FORCE_WIN32_ENV, "1")
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    original = platform._wmi_query
    install_safe_platform()
    assert platform._wmi_query is original


def test_install_is_idempotent_and_noop_off_windows(monkeypatch):
    for name in _PATCHED:
        monkeypatch.setattr(
            platform, name, getattr(platform, name, None), raising=False
        )
    monkeypatch.delattr(platform, safe_platform._MARKER, raising=False)
    monkeypatch.delenv(safe_platform.FORCE_WIN32_ENV, raising=False)
    original = platform._wmi_query
    monkeypatch.setattr(sys, "platform", "linux")
    install_safe_platform()
    assert platform._wmi_query is original
    assert not hasattr(platform, safe_platform._MARKER)

    monkeypatch.setattr(sys, "platform", "win32")
    install_safe_platform()
    first = (platform._wmi_query, platform.win32_ver, platform._uname_cache)
    install_safe_platform()
    assert (platform._wmi_query, platform.win32_ver, platform._uname_cache) == first
    with pytest.raises(OSError):
        platform._wmi_query("OS", "Version")


def test_bounded_platform_call_returns_default_on_hang():
    gate = threading.Event()
    try:
        start = time.monotonic()
        assert bounded_platform_call(lambda: gate.wait(30), 0.2, "unknown") == "unknown"
        assert time.monotonic() - start < _FAST
    finally:
        gate.set()
    assert bounded_platform_call(lambda: "ok", 1.0, "unknown") == "ok"
    assert bounded_platform_call(lambda: 1 / 0, 1.0, "unknown") == "unknown"


def test_entrypoint_installs_before_imports(tmp_path):
    (tmp_path / "sitecustomize.py").write_text(
        "import platform, time\n"
        "platform._wmi_query = lambda *a: time.sleep(30)\n"
        "platform._uname_cache = None\n",
        encoding="utf-8",
    )
    probe = (
        "import meow, platform, time\n"
        "start = time.monotonic()\n"
        "platform.uname(); platform.platform(); platform.win32_ver()\n"
        "print(platform.system(), round(time.monotonic() - start, 1))\n"
    )
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([
            str(tmp_path),
            os.environ.get("PYTHONPATH", ""),
        ]),
        "MEOW_TEST_FORCE_WIN32": "1",
    }
    done = subprocess.run(
        [sys.executable, "-c", probe],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("Windows ")
    helped = subprocess.run(
        [sys.executable, "-m", "meow", "--help"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert helped.returncode == 0, helped.stderr


def test_third_party_user_agent_does_not_hang(hung_wmi):
    install_safe_platform()
    outcome = []

    def build():
        ClaudeSDKClient(options=ClaudeAgentOptions())
        try:
            SubprocessCLITransport(prompt="hi", options=ClaudeAgentOptions())
        except Exception as exc:  # the CLI may be absent; only hanging matters
            outcome.append(type(exc).__name__)

    worker = threading.Thread(target=build, daemon=True)
    worker.start()
    worker.join(5)
    assert not worker.is_alive()
    assert hung_wmi == []
