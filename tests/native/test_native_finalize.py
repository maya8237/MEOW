"""Native completion obeys the shared review and gate predicate."""

import asyncio
import subprocess
from unittest.mock import AsyncMock, patch

from meow.native.native_state import checkpoint, finalize
from meow.execution.run_state import RunStore


def test_native_finalize_requires_review(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "dev", str(tmp_path)], check=True)
    (tmp_path / ".harness.toml").write_text(
        '[[lint]]\ncommand="ruff check"\n', encoding="utf-8"
    )
    run_id = checkpoint(tmp_path, tmp_path, "planned", request="work")["run_id"]
    with (
        patch(
            "meow.native.native_state.run_final_checks",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch("meow.native.native_state.configured_checks", return_value=[]),
    ):
        result = asyncio.run(finalize(tmp_path, tmp_path, run_id))
    assert not result["complete"]
    assert RunStore(tmp_path).load(run_id).phase == "failed"


def test_native_finalize_delivers_after_current_evidence(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "dev", str(tmp_path)], check=True)
    (tmp_path / ".harness.toml").write_text(
        '[[lint]]\ncommand="ruff check"\n', encoding="utf-8"
    )
    run_id = checkpoint(tmp_path, tmp_path, "planned", request="work")["run_id"]
    with (
        patch(
            "meow.native.native_state.run_final_checks",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch("meow.native.native_state.configured_checks", return_value=[]),
        patch("meow.native.native_state.completion_ready", return_value=True),
        patch("meow.native.native_state.deliver_verified_run") as deliver,
    ):
        result = asyncio.run(finalize(tmp_path, tmp_path, run_id))
    assert result["complete"]
    deliver.assert_called_once()
