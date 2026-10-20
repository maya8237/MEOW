import os
import shutil
import subprocess
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest


@pytest.fixture(params=[HTTPStatus.NOT_FOUND, HTTPStatus.SERVICE_UNAVAILABLE])
def checksum_server(request):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"tag_name":"1.2.3"}' if self.path == "/latest.json" else b"zip"
            self.send_response(request.param if self.path.endswith(".txt") else 200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", request.param
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows installer")
@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
def test_checksum_download_error_identifies_release_file(
    tmp_path, shell, checksum_server
):
    executable = shutil.which(shell)
    if executable is None:
        pytest.skip(f"{shell} is unavailable")
    base, status = checksum_server
    env = dict(
        os.environ,
        USERPROFILE=str(tmp_path),
        MEOW_RELEASE_BASE_URL=base,
        MEOW_RELEASE_MANIFEST_URL=f"{base}/latest.json",
    )
    script = Path(__file__).resolve().parents[2] / "scripts" / "install.ps1"
    result = subprocess.run(
        [
            executable,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 1
    assert "checksum file" in result.stdout
    assert "meow-windows-x86_64.zip.txt" in result.stdout
    assert "1.2.3" in result.stdout
    if status == HTTPStatus.NOT_FOUND:
        assert "missing" in result.stdout
    else:
        assert "missing" not in result.stdout
        assert f"{base}/download/1.2.3/" in result.stdout
    assert (tmp_path / ".meow" / "versions").is_dir()
    assert not (tmp_path / ".meow" / "versions" / "1.2.3").exists()
    assert not list((tmp_path / ".meow" / "versions").glob(".install-*"))
