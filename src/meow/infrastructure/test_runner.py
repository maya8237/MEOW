"""Configured command execution and owned dev-server lifecycle."""

import asyncio
import os
import shutil
import signal
import socket
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from meow.project.command_policy import split_command
from meow.project.config_models import (
    DevServerCommand,
    VerificationCommand,
    resolve_command_cwd,
)

MAX_OUTPUT_CHARS = 8000


@dataclass(frozen=True)
class BrowserEvidence:
    provider: str
    kind: str
    status: str
    output: str = ""
    required: bool = False
    reason: str = ""
    flows: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    exit_code: int | None = None


class VerificationSetupError(RuntimeError):
    """A test or server could not be launched or made ready."""

    def __init__(self, command: str, cwd: Path, cause: object):
        self.command = command
        self.cwd = cwd
        self.cause = cause
        super().__init__(f"Could not set up command {command!r} in {cwd}: {cause}")


@dataclass
class _OwnedProcess:
    process: asyncio.subprocess.Process
    job: object | None = None


def _spawn_windows_job_process(  # ruff: ignore[too-many-statements]
    argv, cwd: Path, env: dict[str, str], loop
):
    """Launch suspended, attach the process tree to a job, then resume it."""
    import win32api
    import win32job
    import win32process

    startup = win32process.STARTUPINFO()
    process_handle, thread_handle, pid, _ = win32process.CreateProcess(
        argv[0],
        subprocess.list2cmdline(argv),
        None,
        None,
        False,
        win32process.CREATE_SUSPENDED
        | win32process.CREATE_NEW_PROCESS_GROUP
        | win32process.CREATE_NO_WINDOW,
        env,
        str(cwd),
        startup,
    )
    job = None
    try:
        job = win32job.CreateJobObject(None, "")
        info = win32job.QueryInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation
        )
        info["BasicLimitInformation"]["LimitFlags"] |= (
            win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        win32job.SetInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation, info
        )
        win32job.AssignProcessToJobObject(job, process_handle)
        win32process.ResumeThread(thread_handle)
    except BaseException:
        if job is not None:
            job.Close()
        with suppress(Exception):
            win32api.TerminateProcess(process_handle, 1)
        process_handle.Close()
        raise
    finally:
        thread_handle.Close()
    async_process = _WindowsOwnedProcess(pid, process_handle, loop)
    return async_process, job


class _WindowsOwnedProcess:
    def __init__(self, pid: int, handle, loop):
        self.pid = pid
        self.handle = handle
        self.returncode = None
        self._event = asyncio.Event()
        self._loop = loop
        threading.Thread(target=self._watch, daemon=True).start()

    def _watch(self):
        import win32event
        import win32process

        win32event.WaitForSingleObject(self.handle, win32event.INFINITE)
        code = win32process.GetExitCodeProcess(self.handle)
        self.returncode = code if code < (1 << 31) else code - (1 << 32)
        if not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._event.set)

    async def wait(self):
        if self.returncode is None:
            await self._event.wait()
        return self.returncode

    def kill(self):
        import win32api

        win32api.TerminateProcess(self.handle, 1)


@dataclass(frozen=True)
class VerificationCommandEvidence:
    cwd: Path
    command: str
    exit_code: int | None
    output: str
    timed_out: bool
    gate: bool


@dataclass(frozen=True)
class VerificationStageEvidence:
    commands: tuple[VerificationCommandEvidence, ...] = ()
    server_urls: tuple[str, ...] = ()
    browser: tuple[BrowserEvidence, ...] = ()

    @property
    def blocking_failed(self) -> bool:
        # An unknown exit code is a failure, never a pass.
        return any(
            command.gate and (command.timed_out or command.exit_code != 0)
            for command in self.commands
        ) or any(
            item.required and item.status in {"failed", "unavailable"}
            for item in self.browser
        )


def _argv(command: str, args: tuple[str, ...]) -> list[str]:
    argv = split_command(command) + list(args)
    if argv:
        argv[0] = shutil.which(argv[0]) or argv[0]
    return argv


def _launch_argv(command: str, args: tuple[str, ...]) -> list[str]:
    """`_argv` for a process about to start: no command is a ValueError."""
    argv = _argv(command, args)
    if not argv:
        raise ValueError("empty command")
    return argv


def _decode(data: bytes) -> str:
    output = data.decode("utf-8", errors="replace")
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + "\n… output truncated …"
    return output


async def _terminate(  # ruff: ignore[too-many-branches, complex-structure, too-many-statements]
    owned: asyncio.subprocess.Process | _OwnedProcess,
) -> None:
    process = owned.process if isinstance(owned, _OwnedProcess) else owned
    if isinstance(owned, _OwnedProcess) and owned.job is not None:
        owned.job.Close()
        owned.job = None
        if process.returncode is None:
            with suppress(Exception):
                await asyncio.wait_for(process.wait(), timeout=2)
        return
    if os.name == "nt":
        taskkill = shutil.which("taskkill")
        if taskkill:
            killer = await asyncio.create_subprocess_exec(
                taskkill,
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
        else:
            process.kill()
    else:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
    try:
        await asyncio.wait_for(process.wait(), timeout=2)
    except TimeoutError:
        if os.name == "nt":
            process.kill()
    if os.name != "nt":
        # The leader may exit on SIGTERM while its children ignore it.
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    await process.wait()


async def _run_test(
    active_dir: Path, command: VerificationCommand
) -> VerificationCommandEvidence:
    try:
        cwd = resolve_command_cwd(active_dir, command.cwd)
    except (OSError, ValueError) as exc:
        raise VerificationSetupError(
            command.command, active_dir / command.cwd, exc
        ) from exc
    env = {**os.environ, **(command.env or {})}
    try:
        argv = _launch_argv(command.command, command.args)
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name != "nt",
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            ),
        )
    except (OSError, ValueError) as exc:
        raise VerificationSetupError(command.command, cwd, exc) from exc
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), command.timeout)
        output = _decode(stdout + (b"\n" if stdout and stderr else b"") + stderr)
        return VerificationCommandEvidence(
            cwd, command.command, process.returncode, output, False, command.gate
        )
    except TimeoutError:
        await _terminate(process)
        return VerificationCommandEvidence(
            cwd,
            command.command,
            None,
            f"Timed out after {command.timeout}s",
            True,
            command.gate,
        )
    except asyncio.CancelledError:
        await _terminate(process)
        raise


def _artifact_stamp(root: Path, name: str) -> tuple[int, int] | None:
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        return None
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_size


def _url_env(urls: tuple[str, ...]) -> dict[str, str]:
    """Tell a browser provider where the (possibly relocated) servers listen."""
    if not urls:
        return {}
    env = {"MEOW_DEV_SERVER_URL": urls[0]}
    env["MEOW_DEV_SERVER_URLS"] = os.pathsep.join(urls)
    return env


async def _run_browser(
    active_dir: Path, tester: dict, urls: tuple[str, ...] = ()
) -> BrowserEvidence | None:
    browser = tester.get("browser")
    if not isinstance(browser, dict):
        return None
    required = bool(browser.get("required", False))
    name = str(browser.get("name", ""))
    kind = str(browser.get("kind", ""))
    if kind != "command":
        return BrowserEvidence(
            name,
            kind,
            "unavailable",
            required=required,
            reason="provider requires agent validation",
        )
    if not tester.get("dev_server"):
        return BrowserEvidence(
            name,
            kind,
            "unavailable",
            required=required,
            reason="missing start command and readiness URL",
        )
    artifacts = tuple(str(item) for item in browser.get("artifacts", []))
    before = {item: _artifact_stamp(active_dir, item) for item in artifacts}
    command = VerificationCommand(
        Path(browser.get("cwd", ".")),
        str(browser["entrypoint"]),
        tuple(browser.get("args", [])),
        float(browser.get("timeout", 300)),
        {**(browser.get("env") or {}), **_url_env(urls)},
        required,
    )
    try:
        result = await _run_test(active_dir, command)
    except VerificationSetupError as exc:
        return BrowserEvidence(
            name, kind, "unavailable", required=required, reason=str(exc)
        )
    available_artifacts = tuple(
        item
        for item in artifacts
        if _artifact_stamp(active_dir, item) is not None
        and _artifact_stamp(active_dir, item) != before[item]
    )
    status = "failed" if result.timed_out or result.exit_code != 0 else "passed"
    return BrowserEvidence(
        name,
        kind,
        status,
        result.output,
        required,
        "timed out" if result.timed_out else "",
        tuple(str(item) for item in browser.get("flows", [])),
        available_artifacts,
        result.exit_code,
    )


# Readiness targets a local dev server: never route it through a proxy. A bare
# urlopen consults proxy environment settings.
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _probe(url: str) -> str | None:
    """None when `url` answers; otherwise why it did not.

    Any HTTP response (even 404) means a server is listening; the caller's
    startup deadline bounds the total wait.
    """
    try:
        with _DIRECT.open(url, timeout=1.0):
            return None
    except urllib.error.HTTPError:
        return None
    except (OSError, ValueError) as exc:
        return f"{type(exc).__name__}: {exc}"


def _reachable(url: str) -> bool:
    return _probe(url) is None


_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", ""})


def _url_port(url: str) -> tuple[str, int] | None:
    """Host and port of a loopback readiness URL, or None for any other host."""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    if host not in _LOOPBACK_HOSTS:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    return host, port or (443 if parts.scheme == "https" else 80)


def _port_in_use(host: str, port: int) -> bool:
    """True when something already listens on the loopback port (any protocol)."""
    target = "127.0.0.1" if host in {"", "0.0.0.0", "localhost"} else host
    family = socket.AF_INET6 if ":" in target else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((target, port)) == 0


def _free_port(host: str) -> int:
    target = host if host in {"::1"} else "127.0.0.1"
    family = socket.AF_INET6 if ":" in target else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        probe.bind((target, 0))
        return probe.getsockname()[1]


def _with_port(url: str, port: int) -> str:
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or "localhost"
    netloc = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    return urllib.parse.urlunsplit(parts._replace(netloc=netloc))


def _relocated(server: DevServerCommand, port: int) -> DevServerCommand:
    """The server re-pointed at `port`: `{port}` substituted, `PORT` exported."""

    def fill(text: str) -> str:
        return text.replace("{" + "port}", str(port))

    env = {key: fill(value) for key, value in (server.env or {}).items()}
    env["PORT"] = str(port)
    return DevServerCommand(
        server.cwd,
        fill(server.command),
        tuple(fill(arg) for arg in server.args),
        env,
        _with_port(server.ready_url, port),
        server.startup_timeout,
    )


async def _start_server(  # ruff: ignore[complex-structure, too-many-statements, too-many-branches]
    active_dir: Path, server: DevServerCommand
):
    """Start the server; returns (owned process, the URL it is actually serving)."""
    try:
        cwd = resolve_command_cwd(active_dir, server.cwd)
    except (OSError, ValueError) as exc:
        raise VerificationSetupError(
            server.command, active_dir / server.cwd, exc
        ) from exc
    location = _url_port(server.ready_url)
    taken = (
        await asyncio.to_thread(_port_in_use, *location)
        if location
        else await asyncio.to_thread(_reachable, server.ready_url)
    )
    if taken:
        if location is None:
            raise VerificationSetupError(
                server.command,
                cwd,
                f"readiness URL {server.ready_url} is already serving before launch",
            )
        # Never test against someone else's server: move to a free port.
        server = _relocated(server, _free_port(location[0]))
    try:
        argv = _launch_argv(server.command, server.args)
        if os.name == "nt":
            process, job = await asyncio.to_thread(
                _spawn_windows_job_process,
                argv,
                cwd,
                {**os.environ, **(server.env or {})},
                asyncio.get_running_loop(),
            )
        else:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                env={**os.environ, **(server.env or {})},
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
            job = None
    except Exception as exc:
        raise VerificationSetupError(server.command, cwd, exc) from exc
    try:
        owned = _OwnedProcess(process, job)
    except Exception as exc:
        await _terminate(process)
        raise VerificationSetupError(
            server.command, cwd, f"could not own process tree: {exc}"
        ) from exc
    deadline = asyncio.get_running_loop().time() + server.startup_timeout
    last_error = "never probed"
    try:
        while asyncio.get_running_loop().time() < deadline:
            if process.returncode is not None:
                raise VerificationSetupError(
                    server.command,
                    cwd,
                    f"server exited with code {process.returncode} before "
                    f"{server.ready_url} became ready",
                )
            last_error = await asyncio.to_thread(_probe, server.ready_url)
            if last_error is None:
                return owned, server.ready_url
            await asyncio.sleep(0.1)
        raise VerificationSetupError(
            server.command,
            cwd,
            f"timed out after {server.startup_timeout}s waiting for "
            f"{server.ready_url} (last probe: {last_error})",
        )
    except BaseException:
        await _terminate(owned)
        raise


async def _start_server_with_fallback(active_dir: Path, server: DevServerCommand):
    """Start the server, retrying on a fresh port if it loses a port race.

    The port can be taken between the free-port probe and the server's bind;
    that shows up as an early exit or a readiness timeout, so try new ports
    before reporting the tester stage as unavailable.
    """
    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            return await _start_server(active_dir, server)
        except VerificationSetupError:
            location = _url_port(server.ready_url)
            if attempt == attempts or location is None:
                raise
            server = _relocated(server, _free_port(location[0]))
    raise AssertionError("unreachable")


@asynccontextmanager
async def prepared_test_stage(active_dir: Path, config: dict):
    """Run configured suites and keep owned servers alive for the caller."""
    tester = config.get("tester", {})
    servers: list[_OwnedProcess] = []
    urls: list[str] = []
    try:
        for server in tester.get("dev_server", []):
            owned, url = await _start_server_with_fallback(active_dir, server)
            servers.append(owned)
            urls.append(url)
        commands = []
        for command in tester.get("tests", []):
            commands.append(await _run_test(active_dir, command))
        browser = await _run_browser(active_dir, tester, tuple(urls))
        yield VerificationStageEvidence(
            tuple(commands), tuple(urls), (browser,) if browser else ()
        )
    finally:
        for process in reversed(servers):
            await _terminate(process)
