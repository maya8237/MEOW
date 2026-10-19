from unittest.mock import patch

import pytest

from tests.native.test_native_cli import run_native


@pytest.mark.parametrize(
    "command,target",
    [
        (["custom", "--role", "reviewer"], "meow.native.native.custom"),
        (["run", "review", "abc123"], "meow.native.native_orchestrate.review"),
    ],
)
def test_native_dispatch_preserves_host_capabilities(tmp_path, command, target):
    with patch(target, return_value={}) as operation:
        code, _, error = run_native(
            *command,
            "--work-dir",
            str(tmp_path),
            "--host",
            "codex",
            "--available-model",
            "gpt-a",
            "--available-model",
            "gpt-b",
        )
    assert code == 0, error
    assert operation.call_args.kwargs == {
        "host": "codex",
        "available_models": ["gpt-a", "gpt-b"],
    }


@pytest.mark.parametrize(
    "command,target",
    [
        (["custom", "--role", "reviewer"], "meow.native.native.custom"),
        (["run", "review", "abc123"], "meow.native.native_orchestrate.review"),
    ],
)
def test_native_dispatch_keeps_legacy_defaults(tmp_path, command, target):
    with patch(target, return_value={}) as operation:
        code, _, error = run_native(*command, "--work-dir", str(tmp_path))
    assert code == 0, error
    assert operation.call_args.kwargs == {}
