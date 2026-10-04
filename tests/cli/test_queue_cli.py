from meow.cli.cli import _build_arg_parser
from meow.cli.queue_cli import queue
from meow.execution.queue_state import QueueStore


def test_queue_parser_accepts_request_and_working_dir():
    args = _build_arg_parser().parse_args(["queue", "task", "--working-dir", "."])
    assert args.command == "queue"
    assert args.request == "task"


def test_queue_command_persists_without_running(tmp_path):
    assert queue(tmp_path, "independent task") == 0
    assert QueueStore(tmp_path).list()[0].request == "independent task"
