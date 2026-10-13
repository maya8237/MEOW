"""Operations and public serializers for the local UI session HTTP API."""

from __future__ import annotations

import re
from dataclasses import replace
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from meow.infrastructure import ui_sessions
from meow.project.repos import repo_choices
from meow.ui.sessions import (
    Recipe,
    RecipeError,
    Session,
    SessionError,
    SessionStore,
    command_for,
    detect_capabilities,
    display_command,
    new_id,
    validate_recipe,
)

_ID = re.compile(r"[0-9a-f]{32}\Z")
_ACTIVE = {"starting", "running", "stopping"}
_DETAIL_ROUTE_LENGTH = 4
_ACTION_ROUTE_LENGTH = 5
_WINDOWS_PATH = re.compile(r"(?i)(?<![\w])(?:[a-z]:\\|\\\\)[^\s\"'<>]+")
_POSIX_PATH = re.compile(r"(?<![\w:/])/(?:[^\s/]+/)*[^\s/]+")


class ApiError(ValueError):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status


def _route_parts(path: str) -> list[str]:
    return [unquote(part, errors="strict") for part in path.split("/")]


def _id(value: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid identifier.")
    return value


def _public_error(value: str | None) -> str | None:
    if value is None:
        return None
    value = _WINDOWS_PATH.sub("[path]", value)
    return _POSIX_PATH.sub("[path]", value)


def public_error(error: BaseException | str) -> str:
    """Keep filesystem failures useful without exposing server-side paths."""
    if isinstance(error, PermissionError):
        return "Permission denied while accessing local UI files."
    if isinstance(error, OSError):
        return "A local file operation failed."
    return _public_error(str(error)) or "Request failed."


def _capabilities(repo: Path) -> dict:
    try:
        return detect_capabilities(repo)
    except RecipeError as exc:
        message = str(exc)
        missing = []
        if ": " in message and "missing required capabilities" in message:
            missing = [item.strip() for item in message.split(": ", 1)[1].split(",")]
        return {
            "available": False,
            "version": None,
            "missing": missing,
            "permission_policy": "unsupported",
            "bypass_supported": False,
            "error": message,
        }


def _repo(server, value: str | None, *, required: bool = True) -> Path | None:
    if value is None or value == "":
        if required:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Choose a repo from the list.")
        return None
    choices = repo_choices(server.working_dir)
    if value not in choices:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Choose a repo from the list.")
    return Path(value).resolve()


def _query(url: str) -> dict[str, str]:
    values = parse_qs(urlsplit(url).query, keep_blank_values=True)
    return {key: value[-1] for key, value in values.items()}


def _attempt_summary(attempt) -> dict | None:
    if attempt is None:
        return None
    return {
        "id": attempt.id,
        "state": attempt.state,
        "updated_at": attempt.finished_at or attempt.started_at or attempt.created_at,
        "exit_code": attempt.exit_code,
    }


def _attempt_detail(attempt, repo: Path) -> dict:
    from meow.common.process import process_identity

    resume = ui_sessions.attempt_resume_status(repo, attempt)
    pid = attempt.worker.get("pid")
    identity = attempt.worker.get("identity")
    alive = (
        isinstance(pid, int)
        and isinstance(identity, str)
        and process_identity(pid) == identity
    )
    return {
        "id": attempt.id,
        "state": resume["state"],
        "created_at": attempt.created_at,
        "started_at": attempt.started_at,
        "finished_at": attempt.finished_at,
        "effective_recipe": attempt.effective_recipe,
        "exit_code": attempt.exit_code,
        "output_size": attempt.output_size,
        "claude_session_id": attempt.claude_session_id,
        "run_ids": list(attempt.run_ids),
        "error": _public_error(attempt.error),
        "worker_alive": alive,
        "resume_supported": resume["resume_supported"],
        "resume_needs_request": resume["resume_needs_request"],
    }


def _summary(store: SessionStore, session: Session) -> dict:
    attempts = [store._read_attempt(session.id, item) for item in session.attempts]
    last = attempts[-1] if attempts else None
    state = (
        ui_sessions.attempt_resume_status(store.repo, last)["state"]
        if last
        else session.state
    )
    active = next(
        (attempt.id for attempt in reversed(attempts) if attempt.state in _ACTIVE), None
    )
    recipe = session.recipe
    return {
        "id": session.id,
        "name": recipe["name"],
        "repo": str(store.repo),
        "state": state,
        "mode": recipe["mode"],
        "command": recipe["command"],
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "archived": session.archived,
        "active_attempt_id": active,
        "attempt_count": len(attempts),
        "last_attempt": _attempt_summary(last),
    }


def _detail(store: SessionStore, session: Session) -> dict:
    return {
        **_summary(store, session),
        "recipe": session.recipe,
        "attempts": [
            _attempt_detail(store._read_attempt(session.id, item), store.repo)
            for item in session.attempts
        ],
    }


def _load_session(repo: Path, session_id: str) -> tuple[SessionStore, Session]:
    store = SessionStore(repo)
    try:
        return store, store.load(_id(session_id))
    except SessionError as exc:
        raise ApiError(HTTPStatus.NOT_FOUND, "Session not found.") from exc


def _int_query(query: dict[str, str], key: str, default: int) -> int:
    try:
        return int(query.get(key, default))
    except (TypeError, ValueError) as exc:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{key} must be an integer.") from exc


def get(server, url: str):  # ruff: ignore[complex-structure, too-many-return-statements, too-many-branches, too-many-statements]
    """Return (status, JSON body) or a Download descriptor for an API GET."""
    path = urlsplit(url).path
    query = _query(url)
    parts = _route_parts(path)
    if parts == ["", "api", "capabilities"]:
        repo = _repo(server, query.get("repo"))
        return HTTPStatus.OK, {
            "cli": {"available": True},
            "claude": _capabilities(repo),
        }
    if parts == ["", "api", "sessions"]:
        selected = _repo(server, query.get("repo"), required=False)
        repos = (
            [selected]
            if selected
            else [Path(value).resolve() for value in repo_choices(server.working_dir)]
        )
        sessions = []
        for repo in repos:
            store = SessionStore(repo)
            for session in store.list():
                session = ui_sessions.reconcile_session(repo, session.id)
                sessions.append(_summary(store, session))
        sessions.sort(key=lambda item: item["updated_at"], reverse=True)
        return HTTPStatus.OK, {"sessions": sessions}
    if (
        len(parts) == _DETAIL_ROUTE_LENGTH
        and parts[:2] == ["", "api"]
        and parts[2] == "sessions"
    ):
        repo = _repo(server, query.get("repo"))
        store, session = _load_session(repo, parts[3])
        session = ui_sessions.reconcile_session(repo, session.id)
        return HTTPStatus.OK, {"session": _detail(store, session)}
    if parts == ["", "api", "runs"]:
        selected = _repo(server, query.get("repo"), required=False)
        repos = (
            [selected]
            if selected
            else [Path(value).resolve() for value in repo_choices(server.working_dir)]
        )
        runs = []
        for repo in repos:
            runs.extend(ui_sessions.list_imported_runs(repo))
        runs.sort(key=lambda item: item["updated_at"], reverse=True)
        return HTTPStatus.OK, {"runs": runs}
    if (
        len(parts) == _ACTION_ROUTE_LENGTH
        and parts[:2] == ["", "api"]
        and parts[2] == "sessions"
        and parts[4] in {"output", "download"}
    ):
        repo = _repo(server, query.get("repo"))
        store, _ = _load_session(repo, parts[3])
        attempt_id = _id(query.get("attempt", ""))
        try:
            attempt = store._read_attempt(parts[3], attempt_id)
        except SessionError as exc:
            raise ApiError(HTTPStatus.NOT_FOUND, "Attempt not found.") from exc
        ui_sessions.reconcile_attempt(repo, parts[3], attempt_id)
        attempt = store._read_attempt(parts[3], attempt_id)
        if parts[4] == "output":
            return HTTPStatus.OK, store.read_output(
                parts[3],
                attempt_id,
                _int_query(query, "cursor", 0),
                _int_query(query, "limit", 16 * 1024),
            )
        path = store._directory(parts[3]) / attempt.output_file
        path.touch(exist_ok=True)
        return HTTPStatus.OK, {"download": path, "filename": f"{attempt_id}.txt"}
    if (
        len(parts) == _ACTION_ROUTE_LENGTH
        and parts[:2] == ["", "api"]
        and parts[2] == "runs"
        and parts[4] in {"output", "download"}
    ):
        repo = _repo(server, query.get("repo"))
        run_id = _id(parts[3])
        if parts[4] == "output":
            return HTTPStatus.OK, ui_sessions.read_run_output(
                repo,
                run_id,
                _int_query(query, "cursor", 0),
                _int_query(query, "limit", 16 * 1024),
            )
        path = ui_sessions.run_output_path(repo, run_id)
        if not path.is_file():
            raise ApiError(
                HTTPStatus.NOT_FOUND, "No background output was recorded for this run."
            )
        return HTTPStatus.OK, {"download": path, "filename": f"{run_id}.txt"}
    return None


def _required_recipe(repo: Path, payload: dict) -> Recipe:
    try:
        return validate_recipe(repo, payload.get("recipe"))
    except RecipeError as exc:
        raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc


def _body_repo(server, payload: dict) -> Path:
    value = payload.get("repo")
    if not isinstance(value, str):
        raise ApiError(HTTPStatus.BAD_REQUEST, "Choose a repo from the list.")
    return _repo(server, value)


def post(server, url: str, payload: dict):  # ruff: ignore[complex-structure, too-many-return-statements, too-many-branches, too-many-statements, too-many-locals]
    path = urlsplit(url).path
    parts = _route_parts(path)
    if parts == ["", "api", "sessions", "preview"]:
        repo = _body_repo(server, payload)
        recipe = _required_recipe(repo, payload)
        argv = command_for(recipe)
        warnings = (
            [
                "Run commands may commit and push when the saved delivery policy "
                "allows it."
            ]
            if recipe.command == "run" and recipe.mode == "cli"
            else []
        )
        return HTTPStatus.OK, {
            "valid": True,
            "argv": argv,
            "display": display_command(argv),
            "warnings": warnings,
            "capabilities": _capabilities(repo),
        }
    if parts == ["", "api", "sessions"]:
        repo = _body_repo(server, payload)
        recipe = _required_recipe(repo, payload)
        session = SessionStore(repo).save_recipe(recipe)
        return HTTPStatus.CREATED, {"session": _detail(SessionStore(repo), session)}
    if (
        len(parts) == _DETAIL_ROUTE_LENGTH
        and parts[:2] == ["", "api"]
        and parts[2] == "sessions"
    ):
        return None
    if (
        len(parts) == _ACTION_ROUTE_LENGTH
        and parts[:2] == ["", "api"]
        and parts[2] == "sessions"
    ):
        repo = _body_repo(server, payload)
        session_id = _id(parts[3])
        action = parts[4]
        store, session = _load_session(repo, session_id)
        if action == "launch":
            attempt = ui_sessions.launch_attempt(repo, session_id)
            session = store.load(session_id)
            return HTTPStatus.OK, {
                "session": _detail(store, session),
                "attempt": _attempt_detail(attempt, repo),
            }
        if action == "resume":
            attempt_id = _id(payload.get("attempt_id", ""))
            request = payload.get("request")
            repair = payload.get("repair", False)
            if request is not None and not isinstance(request, str):
                raise ApiError(HTTPStatus.BAD_REQUEST, "request must be a string.")
            if isinstance(request, str) and "\x00" in request:
                raise ApiError(
                    HTTPStatus.BAD_REQUEST, "request cannot contain a NUL character."
                )
            if not isinstance(repair, bool):
                raise ApiError(HTTPStatus.BAD_REQUEST, "repair must be a boolean.")
            old = store._read_attempt(session_id, attempt_id)
            if repair and not old.run_ids:
                raise ApiError(
                    HTTPStatus.BAD_REQUEST,
                    "repair is supported only for MEOW run attempts.",
                )
            if session.archived:
                raise ApiError(
                    HTTPStatus.BAD_REQUEST,
                    "Unarchive this session before resuming it.",
                )
            attempt = ui_sessions.resume_attempt(
                repo, session_id, attempt_id, request, repair
            )
            session = store.load(session_id)
            return HTTPStatus.ACCEPTED, {
                "session": _detail(store, session),
                "attempt": _attempt_detail(attempt, repo),
            }
        if action == "stop":
            attempt_id = session.attempts[-1] if session.attempts else ""
            attempt = ui_sessions.stop_attempt(repo, session_id, _id(attempt_id))
            session = store.load(session_id)
            return HTTPStatus.OK, {
                "session": _detail(store, session),
                "attempt": _attempt_detail(attempt, repo),
            }
        if action == "duplicate":
            recipe = Recipe(**session.recipe)
            duplicate = replace(recipe, id=new_id(), name=f"Copy of {recipe.name}")
            copied = store.save_recipe(duplicate)
            return HTTPStatus.CREATED, {"session": _detail(store, copied)}
        if action in {"archive", "unarchive"}:
            saved = store.archive(session_id, action == "archive")
            return HTTPStatus.OK, {"session": _detail(store, saved)}
        return None
    if (
        len(parts) == _ACTION_ROUTE_LENGTH
        and parts[:2] == ["", "api"]
        and parts[2] == "runs"
    ):
        repo = _body_repo(server, payload)
        run_id = _id(parts[3])
        action = parts[4]
        if action == "resume":
            request = payload.get("request")
            repair = payload.get("repair", False)
            if request is not None and not isinstance(request, str):
                raise ApiError(HTTPStatus.BAD_REQUEST, "request must be a string.")
            if not isinstance(repair, bool):
                raise ApiError(HTTPStatus.BAD_REQUEST, "repair must be a boolean.")
            session, attempt = ui_sessions.resume_imported_run(
                repo, run_id, request, repair
            )
            store = SessionStore(repo)
            session = store.load(session.id)
            return HTTPStatus.ACCEPTED, {
                "session": _detail(store, session),
                "attempt": _attempt_detail(attempt, repo),
            }
        if action == "cancel":
            record = ui_sessions.cancel_imported_run(repo, run_id)
            return HTTPStatus.OK, {
                "run": ui_sessions.serialize_imported_run(repo, record.id)
            }
    return None


def put(server, url: str, payload: dict):
    parts = _route_parts(urlsplit(url).path)
    if (
        len(parts) != _DETAIL_ROUTE_LENGTH
        or parts[:2] != ["", "api"]
        or parts[2] != "sessions"
    ):
        return None
    repo = _body_repo(server, payload)
    session_id = _id(parts[3])
    store, _ = _load_session(repo, session_id)
    recipe = _required_recipe(repo, payload)
    try:
        session = store.update_recipe(session_id, replace(recipe, id=session_id))
    except SessionError as exc:
        raise ApiError(HTTPStatus.NOT_FOUND, "Session not found.") from exc
    return HTTPStatus.OK, {"session": _detail(store, session)}
