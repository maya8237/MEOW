import asyncio
import socket
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from meow.infrastructure import test_runner as runner
from meow.project.config_models import DevServerCommand

REAL_SOCKET = socket.socket
PORT_FROM_ENV = (
    "import os,http.server,socketserver;"
    "socketserver.TCPServer(('127.0.0.1',int(os.environ['PORT'])),"
    "http.server.SimpleHTTPRequestHandler).serve_forever()"
)
SERVE_ARGS = ("-m", "http.server", "{port}", "--bind", "127.0.0.1")


MAX_ATTEMPTS = 10


class FailingSocket:
    """A real socket whose bind/connect raise a chosen error."""

    def __init__(self, sock, error, calls):
        self.sock, self.error, self.calls = sock, error, calls

    def __getattr__(self, name):
        return getattr(self.sock, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.sock.close()

    def bind(self, address):
        self.calls.append(address)
        raise self.error

    def connect_ex(self, address):
        self.calls.append(address)
        raise self.error


def failing(error, calls):
    return lambda *args, **kwargs: FailingSocket(
        REAL_SOCKET(*args, **kwargs), error, calls
    )


def dev_server(args, ready_url, **extra):
    return DevServerCommand(
        Path("."),
        extra.get("command", sys.executable),
        tuple(args),
        extra.get("env"),
        ready_url,
        extra.get("timeout", 15),
    )


def start(server, tmp_path):
    async def go():
        owned, url = await runner._start_server(tmp_path, server)
        await runner._terminate(owned)
        return url

    return asyncio.run(go())


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (OSError(10013, "access forbidden by socket permissions"), "10013"),
        (OSError(10048, "address already in use"), "10048"),
        (PermissionError(13, "Permission denied"), "13"),
    ],
)
def test_bindable_reports_type_and_code_instead_of_raising(error, code):
    with patch.object(socket, "socket", failing(error, [])):
        reason = runner._bindable("127.0.0.1", 3000)
    assert code in reason
    assert type(error).__name__ in reason


def test_bindable_is_none_for_a_real_free_port():
    port = runner._free_port("127.0.0.1")
    assert runner._bindable("127.0.0.1", port) is None


def test_free_port_retries_then_returns_none():
    calls = []
    with patch.object(socket, "socket", failing(OSError(10013, "x"), calls)):
        assert runner._free_port("127.0.0.1") is None
    assert 1 < len(calls) <= MAX_ATTEMPTS


def test_free_port_ipv6_falls_back_to_ipv4():
    def no_ipv6(family, *args, **kwargs):
        if family == socket.AF_INET6:
            raise OSError(97, "Address family not supported")
        return REAL_SOCKET(family, *args, **kwargs)

    with patch.object(socket, "socket", no_ipv6):
        assert isinstance(runner._free_port("::1"), int)


def test_port_in_use_never_raises():
    with patch.object(socket, "socket", failing(OSError(10013, "x"), [])):
        assert runner._port_in_use("127.0.0.1", 3000) is True

    def no_socket(*args, **kwargs):
        raise OSError(24, "too many open files")

    with patch.object(socket, "socket", no_socket):
        assert runner._port_in_use("127.0.0.1", 3000) is True


def test_placeholder_port_is_allocated_before_launch(tmp_path):
    url = start(dev_server(SERVE_ARGS, "http://127.0.0.1:{port}/"), tmp_path)
    assert "{port}" not in url
    assert url.startswith("http://127.0.0.1:")


def test_placeholder_in_env_exports_the_port(tmp_path):
    server = dev_server(
        ["-c", PORT_FROM_ENV.replace("os.environ['PORT']", "os.environ['APP_PORT']")],
        "http://127.0.0.1:{port}/",
        env={"APP_PORT": "{port}"},
    )
    assert "{port}" not in start(server, tmp_path)


def test_fixed_free_port_is_unchanged(tmp_path):
    port = runner._free_port("127.0.0.1")
    server = dev_server(
        ("-m", "http.server", str(port), "--bind", "127.0.0.1"),
        f"http://127.0.0.1:{port}/",
    )
    assert start(server, tmp_path) == f"http://127.0.0.1:{port}/"


def test_fixed_port_taken_still_relocates(tmp_path):
    with REAL_SOCKET() as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        taken = holder.getsockname()[1]
        server = dev_server(["-c", PORT_FROM_ENV], f"http://127.0.0.1:{taken}/")
        url = start(server, tmp_path)
    assert url != f"http://127.0.0.1:{taken}/"


def test_free_by_connect_but_unbindable_fixed_port_is_relocated(tmp_path):
    fixed = runner._free_port("127.0.0.1")
    real = runner._bindable

    def reserved(host, port):
        if port == fixed:
            return "PermissionError [10013]: reserved by the system"
        return real(host, port)

    server = dev_server(["-c", PORT_FROM_ENV], f"http://127.0.0.1:{fixed}/")
    with patch.object(runner, "_bindable", reserved):
        url = start(server, tmp_path)
    assert url != f"http://127.0.0.1:{fixed}/"


def test_unavailable_port_error_has_code_and_hint(tmp_path):
    server = dev_server(SERVE_ARGS, "http://127.0.0.1:{port}/")
    with (
        patch.object(runner, "_free_port", return_value=None),
        patch.object(
            runner, "_bindable", return_value="PermissionError [10013]: denied"
        ),
        pytest.raises(runner.VerificationSetupError) as caught,
    ):
        start(server, tmp_path)
    assert "10013" in str(caught.value)
    assert "excludedportrange" in str(caught.value)


def test_server_ignoring_the_port_times_out_with_flag_hint(tmp_path):
    server = dev_server(
        ["-c", "import time; time.sleep(30)"],
        "http://127.0.0.1:{port}/",
        timeout=1,
    )
    with pytest.raises(runner.VerificationSetupError) as caught:
        start(server, tmp_path)
    assert "port flag" in str(caught.value)
