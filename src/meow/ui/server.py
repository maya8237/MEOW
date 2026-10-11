"""Loopback-only HTTP server for the catalog page."""

import json
import webbrowser
from contextlib import suppress
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from meow.ui.catalog import build_catalog
from meow.ui.create import CreateError, create_definition
from meow.ui.repos import remember, repo_choices

DEFAULT_PORT = 8765
_UI_DIR = Path(__file__).resolve().parent
_PAGE = _UI_DIR / "index.html"
_LOGO = _UI_DIR / "logo.png"
_FONT_DIR = _UI_DIR / "fonts"
_FONTS = {
    f"/fonts/{name}": _FONT_DIR / name
    for name in ("JetBrainsMono-Regular.woff2", "JetBrainsMono-Bold.woff2")
}
_CREATE_PATHS = {
    "/api/agents": "agents",
    "/api/workflows": "workflows",
    "/api/skills": "skills",
}
_MAX_BODY = 64 * 1024


class _Handler(BaseHTTPRequestHandler):
    server: "_Server"

    def do_GET(self):
        if not self._known_host():
            return
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send(HTTPStatus.OK, _PAGE.read_bytes(), "text/html; charset=utf-8")
        elif path == "/logo.png":
            self._send(HTTPStatus.OK, _LOGO.read_bytes(), "image/png")
        elif path in _FONTS:
            self._send(HTTPStatus.OK, _FONTS[path].read_bytes(), "font/woff2")
        elif path == "/api/catalog":
            working_dir = self.server.working_dir
            catalog = {**build_catalog(working_dir), "repos": repo_choices(working_dir)}
            self._json(HTTPStatus.OK, catalog)
        else:
            self._send(HTTPStatus.NOT_FOUND, b"Not found.", "text/plain; charset=utf-8")

    def do_POST(self):
        if not self._known_host() or not self._same_origin():
            return
        kind = _CREATE_PATHS.get(self.path.split("?", 1)[0])
        if kind is None:
            self._send(HTTPStatus.NOT_FOUND, b"Not found.", "text/plain; charset=utf-8")
            return
        try:
            created = create_definition(self.server.working_dir, kind, self._payload())
        except CreateError as exc:
            self._json(HTTPStatus(exc.status), {"error": str(exc)})
        else:
            self._json(HTTPStatus.CREATED, created)

    def _payload(self) -> dict:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if content_type != "application/json":
            raise CreateError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Send JSON.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 0 < length <= _MAX_BODY:
            raise CreateError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Body is too large.")
        try:
            payload = json.loads(self.rfile.read(length))
        except ValueError as exc:
            raise CreateError(HTTPStatus.BAD_REQUEST, "Body must be JSON.") from exc
        if not isinstance(payload, dict):
            raise CreateError(HTTPStatus.BAD_REQUEST, "Body must be an object.")
        return payload

    def _known_host(self) -> bool:
        if self.headers.get("Host") in self.server.hosts:
            return True
        self._send(HTTPStatus.FORBIDDEN, b"Unknown host.", "text/plain; charset=utf-8")
        return False

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None or origin in self.server.origins:
            return True
        self._json(HTTPStatus.FORBIDDEN, {"error": "Cross-origin request."})
        return False

    def _json(self, status: HTTPStatus, body: dict) -> None:
        self._send(status, json.dumps(body).encode("utf-8"), "application/json")

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
        self.origins = {f"http://{host}" for host in self.hosts}


def serve_ui(
    working_dir: Path, port: int = DEFAULT_PORT, open_browser: bool = True
) -> int:
    remember(Path(working_dir))
    with _Server(port, Path(working_dir)) as server:
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"MEOW catalog at {url} (Ctrl+C to stop)")
        if open_browser:
            webbrowser.open(url)
        with suppress(KeyboardInterrupt):
            server.serve_forever()
    return 0
