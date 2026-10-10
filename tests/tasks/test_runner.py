"""Task checks must pass before their commits can be integrated."""

import asyncio
import sys

import pytest

from meow.tasks.model import TaskSpec
from meow.tasks.runner import verify_task


def test_declared_task_verification_records_success(tmp_path):
    spec = TaskSpec("api", (), ("src/api",), (f'{sys.executable} -c "print(123)"',))
    evidence = asyncio.run(verify_task(spec, tmp_path))
    assert evidence[0]["exit_code"] == 0
    assert "123" in evidence[0]["output"]


def test_failed_task_verification_blocks_integration(tmp_path):
    spec = TaskSpec("api", (), ("src/api",), (f'{sys.executable} -c "exit(7)"',))
    with pytest.raises(RuntimeError, match="verification failed"):
        asyncio.run(verify_task(spec, tmp_path))
