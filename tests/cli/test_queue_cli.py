import subprocess

import pytest

from meow.cli.cli import _build_arg_parser, cli_main
from meow.cli.queue_cli import queue
from meow.execution.queue_state import QueueStore


def test_queue_parser_accepts_request_and_working_dir():
    args = _build_arg_parser().parse_args(["queue", "task", "--work-dir", "."])
    assert args.command == "queue"
    assert args.request == "task"


def test_queue_command_persists_without_running(tmp_path):
    assert queue(tmp_path, "independent task") == 0
    assert QueueStore(tmp_path).list()[0].request == "independent task"


def test_queue_enqueues_through_the_real_cli_entry(tmp_path):
    """`meow queue "task"` used to die on the missing `--no-worktree` flag."""
    subprocess.run(["git", "init", "-q", "-b", "dev", str(tmp_path)], check=True)
    with pytest.raises(SystemExit) as exit_info:
        cli_main(["queue", "task", "--work-dir", str(tmp_path)])
    assert exit_info.value.code == 0
    assert [item.request for item in QueueStore(tmp_path).list()] == ["task"]
