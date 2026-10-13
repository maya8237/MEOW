"""Loopback-only HTTP server for the catalog page."""

import json
import tomllib
import webbrowser
from contextlib import suppress
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from meow.project.config import user_config_path
from meow.project.repos import remember, repo_choices
from meow.ui.catalog import build_global_catalog
from meow.ui.create import CreateError, create_definition

DEFAULT_PORT = 8765
_UI_DIR = Path(__file__).resolve().parent
_STATIC_DIR = _UI_DIR / "static"
_PAGE = _STATIC_DIR / "index.html"
_LOGO = _UI_DIR / "logo.png"
_FONT_DIR = _UI_DIR / "fonts"
_FONTS = {
    f"/fonts/{name}": _FONT_DIR / name
    for name in ("JetBrainsMono-Regular.woff2", "JetBrainsMono-Bold.woff2")
}
_FILES = {
    "/logo.png": (_LOGO, "image/png"),
    **{name: (path, "font/woff2") for name, path in _FONTS.items()},
}
_CREATE_PATHS = {
    "/api/agents": "agents",
    "/api/workflows": "workflows",
    "/api/skills": "skills",
}
_MAX_BODY = 64 * 1024


def _static_files() -> dict[str, tuple[Path, str]]:
    return {
        **_FILES,
        **{
            f"/assets/{path.name}": (
                path,
                "text/css; charset=utf-8"
                if path.suffix == ".css"
                else "text/javascript; charset=utf-8",
            )
            for path in (_STATIC_DIR / "assets").glob("*")
            if path.suffix in {".js", ".css"}
        },
    }


def _config_skill_folders(working_dir: Path) -> list[Path]:
    folders = []
    for config in (
        working_dir / ".meow" / "config.toml",
        working_dir / ".meow" / "config.local.toml",
    ):
        try:
            data = tomllib.loads(config.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        custom = data.get("custom")
        skills = custom.get("skills", []) if isinstance(custom, dict) else []
        folders.extend(
            working_dir / folder for folder in skills if isinstance(folder, str)
        )
    return folders


def _fingerprint(repos: list[str]) -> tuple:
    roots = [user_config_path().parent]
    for repo in repos:
        roots.append(Path(repo) / ".meow")
        roots.extend(_config_skill_folders(Path(repo)))
    stamps = []
    for root in roots:
        paths = [root, *root.rglob("*")] if root.is_dir() else [root]
        for path in paths:
            try:
                info = path.stat()
            except OSError:
                stamps.append((str(path), None))
                continue
            stamps.append((str(path), info.st_mtime_ns, info.st_size))
    return tuple(stamps)


class _Handler(BaseHTTPRequestHandler):
    server: "_Server"

    def do_GET(self):  # ruff: ignore[complex-structure, too-many-branches, too-many-statements]
        if not self._known_host():
            return
        from meow.ui import session_api
        from meow.ui.sessions import RecipeError, SessionError

        path = urlsplit(self.path).path
        files = _static_files()
        try:
            result = session_api.get(self.server, self.path)
            if result is not None:
                status, body = result
                if isinstance(body, dict) and "download" in body:
                    self._download(status, body["download"], body["filename"])
                else:
                    self._json(status, body)
                return
        except (session_api.ApiError, CreateError, RecipeError, SessionError) as exc:
            self._json(
                HTTPStatus(getattr(exc, "status", HTTPStatus.BAD_REQUEST)),
                {"error": session_api.public_error(exc)},
            )
            return
        except (OSError, ValueError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": session_api.public_error(exc)})
            return
        if path == "/api/catalog":
            self._catalog()
        elif path == "/":
            self.server.forget_catalog()
            self._send(HTTPStatus.OK, _PAGE.read_bytes(), "text/html; charset=utf-8")
        elif path in files:
            asset, content_type = files[path]
            self._send(HTTPStatus.OK, asset.read_bytes(), content_type)
        else:
            self._send(HTTPStatus.NOT_FOUND, b"Not found.", "text/plain; charset=utf-8")

    def _catalog(self):
        from meow.ui import session_api

        repo = parse_qs(urlsplit(self.path).query).get("repo", [""])[0]
        try:
            body = self.server.catalog_bytes(repo or None)
        except CreateError as exc:
            self._json(HTTPStatus(exc.status), {"error": session_api.public_error(exc)})
        else:
            self._send(HTTPStatus.OK, body, "application/json")

    def do_POST(self):  # ruff: ignore[complex-structure, too-many-statements]
        if not self._known_host() or not self._same_origin():
            return
        from meow.ui import session_api
        from meow.ui.sessions import RecipeError, SessionBusy, SessionError

        try:
            payload = self._payload()
            result = session_api.post(self.server, self.path, payload)
            if result is not None:
                self._json(*result)
                return
            kind = _CREATE_PATHS.get(urlsplit(self.path).path)
            if kind is None:
                self._send(
                    HTTPStatus.NOT_FOUND, b"Not found.", "text/plain; charset=utf-8"
                )
                return
            created = create_definition(self.server.working_dir, kind, payload)
        except (session_api.ApiError, CreateError, RecipeError, SessionBusy) as exc:
            self._json(
                HTTPStatus(
                    getattr(
                        exc,
                        "status",
                        HTTPStatus.BAD_REQUEST
                        if not isinstance(exc, SessionBusy)
                        else HTTPStatus.CONFLICT,
                    )
                ),
                {"error": session_api.public_error(exc)},
            )
        except SessionError as exc:
            self._json(HTTPStatus.CONFLICT, {"error": session_api.public_error(exc)})
        except (OSError, ValueError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": session_api.public_error(exc)})
        else:
            self._json(HTTPStatus.CREATED, created)

    def do_PUT(self):
        if not self._known_host() or not self._same_origin():
            return
        from meow.ui import session_api
        from meow.ui.sessions import RecipeError, SessionBusy, SessionError

        try:
            payload = self._payload()
            result = session_api.put(self.server, self.path, payload)
            if result is None:
                self._send(
                    HTTPStatus.NOT_FOUND, b"Not found.", "text/plain; charset=utf-8"
                )
            else:
                self._json(*result)
        except (session_api.ApiError, CreateError, RecipeError) as exc:
            self._json(
                HTTPStatus(getattr(exc, "status", HTTPStatus.BAD_REQUEST)),
                {"error": session_api.public_error(exc)},
            )
        except (SessionBusy, SessionError) as exc:
            self._json(HTTPStatus.CONFLICT, {"error": session_api.public_error(exc)})
        except (OSError, ValueError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": session_api.public_error(exc)})

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

    def _download(self, status: HTTPStatus, path: Path, filename: str) -> None:
        try:
            size = path.stat().st_size
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header(
                "Content-Disposition", f'attachment; filename="{filename}"'
            )
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with path.open("rb") as stream:
                while chunk := stream.read(64 * 1024):
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

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
        self._catalogs: dict[str, tuple[tuple, bytes]] = {}

    def forget_catalog(self) -> None:
        self._catalogs.clear()

    def catalog_bytes(self, repo: str | None = None) -> bytes:
        repos = repo_choices(self.working_dir)
        if repo is None:
            scope = repos
        elif repo in repos:
            scope = [repo]
        else:
            raise CreateError(HTTPStatus.BAD_REQUEST, "Choose a repo from the list.")
        stamp = _fingerprint(scope)
        cached = self._catalogs.get(repo or "")
        if cached is None or cached[0] != stamp:
            catalog = {**build_global_catalog(scope), "repos": repos}
            cached = (stamp, json.dumps(catalog).encode("utf-8"))
            self._catalogs[repo or ""] = cached
        return cached[1]


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
