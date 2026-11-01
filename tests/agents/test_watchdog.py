import asyncio
import time
from unittest.mock import patch

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

from meow.agents import base
from meow.agents.base import ProjectContext, SessionAgent
from meow.execution.run_failures import AgentStalled, classify_failure
from meow.execution.run_state import RunStore

LIMITS = {"agent_idle_timeout": 0.05, "agent_result_grace": 0.05}


def _result(*, error=False):
    return ResultMessage(
        subtype="error_during_execution" if error else "success",
        duration_ms=0,
        duration_api_ms=0,
        is_error=error,
        num_turns=1,
        session_id="s",
        result="done",
    )


async def _silent():
    await asyncio.Event().wait()
    yield None  # never reached


async def _result_then_hang(*, error=False):
    yield AssistantMessage(content=[TextBlock("hi")], model="m")
    yield _result(error=error)
    await asyncio.Event().wait()


def _options(tmp_path):
    return ClaudeAgentOptions(cwd=str(tmp_path))


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(base, "_STREAM_POLL_SECONDS", 0.01)
    monkeypatch.setattr(
        base,
        "_run_setting",
        lambda _config, _cwd, key, default: LIMITS.get(key, default),
    )


async def _hello_then_hang():
    yield AssistantMessage(content=[TextBlock("hi")], model="m")
    await asyncio.Event().wait()


def test_idle_query_kills_the_cli_and_parks(tmp_path):
    with (
        patch.object(base, "query", lambda **_: _hello_then_hang()),
        patch.object(base, "_sdk_children", side_effect=[{}, {4242: object()}]),
        patch.object(base, "kill_process_tree") as kill,
        pytest.raises(AgentStalled, match=r"generator.*0\.05s.*agent_idle"),
    ):
        asyncio.run(base.Agent.run_query("go", _options(tmp_path), "generator"))
    kill.assert_called_once_with(4242)
    assert classify_failure(AgentStalled("x")) == ("transient", "agent_stalled")
    assert not base._CLAIMED_PIDS


def test_stalled_query_kills_only_its_own_cli_beside_a_concurrent_one(tmp_path):
    children = {}

    def stream(pid, *, stall):
        async def run():
            children[pid] = object()
            yield AssistantMessage(content=[TextBlock("hi")], model="m")
            if stall:
                await asyncio.Event().wait()
            await asyncio.sleep(0.03)
            yield _result()

        return run()

    streams = iter([stream(1, stall=True), stream(2, stall=False)])

    async def both():
        async def healthy():
            await asyncio.sleep(0.02)  # starts once the stalled CLI is attributed
            return await base.Agent.run_query("b", _options(tmp_path), "reviewer")

        stalled = base.Agent.run_query("a", _options(tmp_path), "tester")
        return await asyncio.gather(stalled, healthy(), return_exceptions=True)

    with (
        patch.object(base, "query", lambda **_: next(streams)),
        patch.object(base, "_sdk_children", lambda: dict(children)),
        patch.object(base, "kill_process_tree") as kill,
        patch.dict(LIMITS, {"agent_idle_timeout": 0.1, "agent_result_grace": 1}),
    ):
        stalled, healthy = asyncio.run(both())
    assert isinstance(stalled, AgentStalled)
    assert healthy == "hi"
    assert [call.args for call in kill.call_args_list] == [(1,)]


def test_ambiguous_query_pid_kills_nothing_but_still_parks(tmp_path):
    with (
        patch.object(base, "query", lambda **_: _hello_then_hang()),
        patch.object(
            base, "_sdk_children", side_effect=[{}, {7: object(), 8: object()}]
        ),
        patch.object(base, "kill_process_tree") as kill,
        pytest.raises(AgentStalled),
    ):
        asyncio.run(base.Agent.run_query("go", _options(tmp_path), "generator"))
    kill.assert_not_called()


def test_query_stalled_before_any_message_kills_nothing(tmp_path):
    with (
        patch.object(base, "query", lambda **_: _silent()),
        patch.object(base, "_sdk_children", lambda: {9: object()}),
        patch.object(base, "kill_process_tree") as kill,
        pytest.raises(AgentStalled),
    ):
        asyncio.run(base.Agent.run_query("go", _options(tmp_path), "generator"))
    kill.assert_not_called()


def test_successful_result_is_accepted_when_stream_never_closes(tmp_path):
    with (
        patch.object(base, "query", lambda **_: _result_then_hang()),
        patch.object(base, "kill_process_tree"),
    ):
        text = asyncio.run(base.Agent.run_query("go", _options(tmp_path), "generator"))
    assert text == "hi"


def test_failed_result_that_never_closes_parks(tmp_path):
    with (
        patch.object(base, "query", lambda **_: _result_then_hang(error=True)),
        patch.object(base, "kill_process_tree"),
        pytest.raises(RuntimeError, match="generator failed"),
    ):  # the failed result itself is raised before the grace matters
        asyncio.run(base.Agent.run_query("go", _options(tmp_path), "generator"))


class _Client:
    def __init__(self, stream):
        self.stream = stream
        process = type("P", (), {"pid": 77})()
        self._transport = type("T", (), {"_process": process})()

    async def query(self, message):
        pass

    def receive_response(self):
        return self.stream

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        pass


def _session(tmp_path, monkeypatch, stream_factory):
    clients = []

    def make(**_):
        clients.append(_Client(stream_factory()))
        return clients[-1]

    monkeypatch.setattr(base, "ClaudeSDKClient", make)
    agent = SessionAgent(ProjectContext(tmp_path, {}))
    agent.role = "generator"
    agent.open(_options(tmp_path))
    return agent, clients


def test_idle_session_turn_kills_the_client_process(tmp_path, monkeypatch):
    agent, _ = _session(tmp_path, monkeypatch, _silent)
    with (
        patch.object(base, "kill_process_tree") as kill,
        pytest.raises(AgentStalled, match="generator sent no SDK message"),
    ):
        asyncio.run(agent.send("go"))
    kill.assert_called_once_with(77)


def test_session_accepts_good_result_then_replaces_the_client(tmp_path, monkeypatch):
    agent, clients = _session(tmp_path, monkeypatch, _result_then_hang)

    async def turn():
        async with agent:
            return await asyncio.wait_for(agent.send("go"), 10)

    with patch.object(base, "kill_process_tree") as kill:
        assert asyncio.run(turn()) == "hi"
    kill.assert_called_once_with(77)
    assert len(clients) > 1  # the killed client was swapped for a fresh one
    assert "replaced" in agent._recap


def test_activity_stamp_is_throttled_and_never_aborts(tmp_path, monkeypatch):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt", request="x", repo=tmp_path, worktree=tmp_path, branch="b"
    )
    context = ProjectContext(
        tmp_path, {"_run_journal": (store, record.id), "models": {}}
    )
    options = base.Agent(context).options(
        system_prompt="s", allowed_tools=[], role="Generator"
    )
    tool = AssistantMessage(content=[ToolUseBlock("t", "Bash", {})], model="m")
    base.log_stream_message("Generator", tool, options=options)
    first = store.load(record.id).results["activity"]
    assert (first["role"], first["event"]) == ("generator", "Bash")
    base.log_stream_message("Generator", _result(), options=options)  # throttled
    assert store.load(record.id).results["activity"] == first
    monkeypatch.setattr(base, "_ACTIVITY_INTERVAL", 0)
    monkeypatch.setattr(store, "record_activity", lambda *_: 1 / 0)
    base.log_stream_message("Generator", _result(), options=options)  # swallowed


def test_record_activity_stamps_the_current_time(tmp_path):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt", request="x", repo=tmp_path, worktree=tmp_path, branch="b"
    )
    before = time.time()
    store.record_activity(record.id, "reviewer", "result")
    assert store.load(record.id).results["activity"]["at"] >= before
