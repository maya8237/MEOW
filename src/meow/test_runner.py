"""Configured command execution and owned dev-server lifecycle."""

import asyncio
import os
import shlex
import shutil
import signal
import subprocess
import urllib.error
import urllib.request
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from meow.config import DevServerCommand, TestCommand, resolve_command_cwd

MAX_OUTPUT_CHARS = 8000


class TesterSetupError(RuntimeError):
    """A test or server could not be launched or made ready."""

    def __init__(self, command: str, cwd: Path, cause: object):
        self.command = command
        self.cwd = cwd
        self.cause = cause
        super().__init__(f"Could not set up command {command!r} in {cwd}: {cause}")


@dataclass(frozen=True)
class TestCommandEvidence:
    cwd: Path
    command: str
    exit_code: int | None
    output: str
    timed_out: bool
    gate: bool


@dataclass(frozen=True)
class TestStageEvidence:
    commands: tuple[TestCommandEvidence, ...] = ()
    server_urls: tuple[str, ...] = ()

    @property
    def blocking_failed(self) -> bool:
        return any(
            command.gate and (command.timed_out or command.exit_code not in {0, None})
            for command in self.commands
        )


def _argv(command: str, args: tuple[str, ...]) -> list[str]:
    argv = shlex.split(command, posix=os.name != "nt") + list(args)
    if argv:
        argv[0] = shutil.which(argv[0]) or argv[0]
    return argv


def _decode(data: bytes) -> str:
    output = data.decode("utf-8", errors="replace")
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + "\n… output truncated …"
    return output


async def _terminate(  # ruff: ignore[too-many-branches]
    process: asyncio.subprocess.Process,
) -> None:
    if process.returncode is not None:
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
        else:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        await process.wait()


async def _run_test(active_dir: Path, command: TestCommand) -> TestCommandEvidence:
    try:
        cwd = resolve_command_cwd(active_dir, command.cwd)
    except (OSError, ValueError) as exc:
        raise TesterSetupError(command.command, active_dir / command.cwd, exc) from exc
    argv = _argv(command.command, command.args)
    env = {**os.environ, **(command.env or {})}
    try:
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
        raise TesterSetupError(command.command, cwd, exc) from exc
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), command.timeout)
        output = _decode(stdout + (b"\n" if stdout and stderr else b"") + stderr)
        return TestCommandEvidence(
            cwd, command.command, process.returncode, output, False, command.gate
        )
    except TimeoutError:
        await _terminate(process)
        return TestCommandEvidence(
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


def _reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=0.25):
            return True
    except (OSError, urllib.error.URLError, ValueError):
        return False


async def _start_server(  # ruff: ignore[complex-structure, too-many-statements]
    active_dir: Path, server: DevServerCommand
):
    try:
        cwd = resolve_command_cwd(active_dir, server.cwd)
    except (OSError, ValueError) as exc:
        raise TesterSetupError(server.command, active_dir / server.cwd, exc) from exc
    if await asyncio.to_thread(_reachable, server.ready_url):
        raise TesterSetupError(
            server.command,
            cwd,
            f"readiness URL {server.ready_url} is already serving before launch",
        )
    argv = _argv(server.command, server.args)
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env={**os.environ, **(server.env or {})},
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=os.name != "nt",
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            ),
        )
    except (OSError, ValueError) as exc:
        raise TesterSetupError(server.command, cwd, exc) from exc
    deadline = asyncio.get_running_loop().time() + server.startup_timeout
    try:
        while asyncio.get_running_loop().time() < deadline:
            if process.returncode is not None:
                raise TesterSetupError(
                    server.command,
                    cwd,
                    f"server exited with code {process.returncode} before "
                    f"{server.ready_url} became ready",
                )
            if await asyncio.to_thread(_reachable, server.ready_url):
                return process
            await asyncio.sleep(0.1)
        raise TesterSetupError(
            server.command,
            cwd,
            f"timed out after {server.startup_timeout}s waiting for {server.ready_url}",
        )
    except BaseException:
        await _terminate(process)
        raise


@asynccontextmanager
async def prepared_test_stage(active_dir: Path, config: dict):
    """Run configured suites and keep owned servers alive for the caller."""
    tester = config.get("tester", {})
    servers: list[asyncio.subprocess.Process] = []
    try:
        for server in tester.get("dev_server", []):
            servers.append(await _start_server(active_dir, server))
        commands = []
        for command in tester.get("tests", []):
            commands.append(await _run_test(active_dir, command))
        urls = tuple(server.ready_url for server in tester.get("dev_server", []))
        yield TestStageEvidence(tuple(commands), urls)
    finally:
        for process in reversed(servers):
            await _terminate(process)
