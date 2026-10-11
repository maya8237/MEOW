"""Loopback-only, read-only HTTP server for the catalog page."""

import json
import webbrowser
from contextlib import suppress
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from meow.ui.catalog import build_catalog

DEFAULT_PORT = 8765
_PAGE = Path(__file__).resolve().parent / "index.html"


class _Handler(BaseHTTPRequestHandler):
    server: "_Server"

    def do_GET(self):
        if self.headers.get("Host") not in self.server.hosts:
            self._send(
                HTTPStatus.FORBIDDEN, b"Unknown host.", "text/plain; charset=utf-8"
            )
            return
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send(HTTPStatus.OK, _PAGE.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/catalog":
            body = json.dumps(build_catalog(self.server.working_dir), indent=2)
            self._send(HTTPStatus.OK, body.encode("utf-8"), "application/json")
        else:
            self._send(HTTPStatus.NOT_FOUND, b"Not found.", "text/plain; charset=utf-8")

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def log_message(*_args):
        return


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, working_dir: Path):
        super().__init__(("127.0.0.1", port), _Handler)
        self.working_dir = working_dir
        self.hosts = {
            f"127.0.0.1:{self.server_port}",
            f"localhost:{self.server_port}",
        }


def serve_ui(
    working_dir: Path, port: int = DEFAULT_PORT, open_browser: bool = True
) -> int:
    with _Server(port, Path(working_dir)) as server:
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"MEOW catalog at {url} (Ctrl+C to stop)")
        if open_browser:
            webbrowser.open(url)
        with suppress(KeyboardInterrupt):
            server.serve_forever()
    return 0
