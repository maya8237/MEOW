import json
import os
import shutil
import subprocess
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

GOOD_DIGEST = "a" * 64


@pytest.fixture(params=[HTTPStatus.NOT_FOUND, HTTPStatus.SERVICE_UNAVAILABLE])
def source_server(request):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/latest.json":
                manifest = {"tag_name": "1.2.3", "wheel_sha256": GOOD_DIGEST}
                body = json.dumps(manifest).encode()
                self.send_response(200)
            else:
                body = b"index failure"
                self.send_response(request.param)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _install(tmp_path, shell, base, **extra):
    executable = shutil.which(shell)
    if executable is None:
        pytest.skip(f"{shell} is unavailable")
    env = {
        **os.environ,
        "USERPROFILE": str(tmp_path),
        "MEOW_PYTHON": sys.executable,
        "PIP_INDEX_URL": f"{base}/simple",
        "PIP_RETRIES": "0",
        "PIP_NO_INPUT": "1",
        "MEOW_RELEASE_MANIFEST_URL": f"{base}/latest.json",
        **extra,
    }
    script = Path(__file__).resolve().parents[2] / "scripts" / "install.ps1"
    return subprocess.run(
        [executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows installer")
@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
def test_index_failure_names_the_release_and_leaves_nothing(
    tmp_path, shell, source_server
):
    result = _install(tmp_path, shell, source_server)

    assert result.returncode == 1
    assert "package index" in result.stdout
    assert "1.2.3" in result.stdout
    assert not (tmp_path / ".meow" / "versions" / "1.2.3").exists()
    assert not list((tmp_path / ".meow" / "versions").glob(".install-*"))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows installer")
@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
def test_missing_python_stops_before_installing(tmp_path, shell, source_server):
    result = _install(tmp_path, shell, source_server, MEOW_PYTHON=str(tmp_path / "x"))

    assert result.returncode == 1
    assert "Python 3.12 or newer" in result.stdout
    assert not (tmp_path / ".meow" / "versions" / "1.2.3").exists()
