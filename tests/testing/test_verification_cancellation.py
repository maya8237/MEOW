"""Cancellation must stop real verification commands and their descendants."""

import asyncio
import sys

import pytest

from meow.infrastructure.checks import run_final_checks
from meow.project.config_models import LintCommand
from meow.tasks.model import TaskSpec
from meow.tasks.runner import verify_task


@pytest.mark.parametrize("kind", ["lint", "build", "task", "lint_hook"])
def test_cancelled_verification_cannot_write_after_returning(tmp_path, kind):  # ruff: ignore[too-many-statements] -- exercise real process cancellation end to end
    from meow.infrastructure.checks import Check

    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow/config.toml").write_text("", encoding="utf-8")
    child = (
        "import time; from pathlib import Path; time.sleep(1); Path('survivor').touch()"
    )
    script = tmp_path / "verify.py"
    script.write_text(
        "import subprocess, sys, time\nfrom pathlib import Path\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
        "Path('ready').touch()\ntime.sleep(2)\n",
        encoding="utf-8",
    )
    config = {"lint": [], "lint_timeout": 10, "build": [], "tester": {"tests": []}}
    if kind == "lint":
        config["lint"] = [LintCommand(sys.executable, args=(str(script),))]
    if kind == "build":
        config["build"] = [Check("build", "build-1", sys.executable, (str(script),))]

    async def scenario():
        operation = (
            verify_task(
                TaskSpec("check", (), ("src",), (f'"{sys.executable}" verify.py',)),
                tmp_path,
            )
            if kind == "task"
            else run_final_checks(tmp_path, config)
        )
        if kind == "lint_hook":
            from meow.infrastructure.lint import _run_subprocess

            operation.close()
            operation = _run_subprocess(tmp_path, [sys.executable, str(script)], 10)
        task = asyncio.create_task(operation)
        try:
            async with asyncio.timeout(10):
                while not (tmp_path / "ready").exists():
                    await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.sleep(1.3)
            assert not (tmp_path / "survivor").exists()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.skipif(sys.platform != "win32", reason="Windows job ownership")
def test_verification_stops_children_after_the_wrapper_has_exited(tmp_path):
    from meow.infrastructure.checks import run_captured_async

    child = (
        "import time; from pathlib import Path; time.sleep(1); Path('survivor').touch()"
    )
    code = f"import subprocess,sys; subprocess.Popen([sys.executable, '-c', {child!r}])"

    async def scenario():
        import os

        result = await run_captured_async(
            [sys.executable, "-c", code], tmp_path, os.environ.copy(), 0.5
        )
        await asyncio.sleep(1.3)
        assert not (tmp_path / "survivor").exists()
        assert result[0] == 0 or result[2]

    asyncio.run(scenario())
