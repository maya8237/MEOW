"""Test and server commands that cannot launch or leave survivors."""

import asyncio
import sys
import time
from pathlib import Path

import pytest

from meow.infrastructure.test_runner import (
    VerificationSetupError,
    _terminate,
    prepared_test_stage,
)
from meow.project.config_models import VerificationCommand

MAX_SECONDS = 20


@pytest.mark.parametrize("command", ['"unterminated', "", "   "])
def test_unlaunchable_test_command_is_a_setup_error(tmp_path, command):
    config = {
        "tester": {"tests": [VerificationCommand(Path("."), command)], "dev_server": []}
    }

    async def run():
        async with prepared_test_stage(tmp_path, config):
            pass

    with pytest.raises(VerificationSetupError):
        asyncio.run(run())


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
def test_terminate_kills_children_that_outlive_a_leader_exiting_on_sigterm(tmp_path):
    marker = tmp_path / "survivor"
    child = (
        "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        f"time.sleep(2);open(r'{marker}','w').close()"
    )
    leader = (
        "import subprocess,sys,time;"
        f"subprocess.Popen([sys.executable,'-c',{child!r}]);"
        "time.sleep(60)"
    )

    async def run():
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", leader, start_new_session=True
        )
        await asyncio.sleep(0.5)
        started = time.monotonic()
        await _terminate(process)
        assert time.monotonic() - started < MAX_SECONDS

    asyncio.run(run())
    time.sleep(3)
    assert not marker.exists()
