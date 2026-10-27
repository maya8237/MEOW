import asyncio
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    CLIConnectionError,
    ResultMessage,
    TextBlock,
)

from meow.agents import base
from meow.agents.base import ProjectContext, SessionAgent


@pytest.mark.parametrize("subtype", ["error_max_turns", "error_during_execution"])
def test_session_send_rejects_failed_sdk_result(tmp_path, monkeypatch, subtype):
    async def responses():  # ruff: ignore[unused-async] -- SDK async stream
        yield ResultMessage(
            subtype=subtype,
            duration_ms=0,
            duration_api_ms=0,
            is_error=True,
            num_turns=1,
            session_id="failed-session",
            result="Context exhausted",
        )

    client = MagicMock()
    client.query = AsyncMock()
    client.receive_response = responses
    monkeypatch.setattr("meow.agents.base.ClaudeSDKClient", lambda **_: client)
    agent = SessionAgent(ProjectContext(tmp_path, {}))
    agent.role = "generator"
    agent.open(ClaudeAgentOptions())
    with pytest.raises(RuntimeError, match=f"generator failed: {subtype}"):
        asyncio.run(agent.send("Implement the plan"))


def _result():
    return ResultMessage(
        subtype="success",
        duration_ms=0,
        duration_api_ms=0,
        is_error=False,
        num_turns=1,
        session_id="s",
        result="done",
    )


class _HangingThenHealthyClient:
    """The first session's CLI dies mid-turn without closing the stream."""

    instances: ClassVar[list] = []

    def __init__(self, **_options):
        self.query = AsyncMock()
        self.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        pass

    def receive_response(self):
        return self._hang() if self is self.instances[0] else self._answer()

    @staticmethod
    async def _hang():
        await asyncio.Event().wait()
        yield None  # never reached

    @staticmethod
    async def _answer():
        yield AssistantMessage(content=[TextBlock("recovered")], model="m")
        yield _result()


def test_dead_cli_mid_turn_recovers(tmp_path, monkeypatch):
    _HangingThenHealthyClient.instances = []
    monkeypatch.setattr(base, "ClaudeSDKClient", _HangingThenHealthyClient)
    monkeypatch.setattr(base, "_STREAM_POLL_SECONDS", 0.01)
    monkeypatch.setattr(base, "_SDK_CRASH_RETRY_BACKOFF", 0)
    monkeypatch.setattr(
        base,
        "_transport_alive",
        lambda client: client is not _HangingThenHealthyClient.instances[0],
    )
    agent = SessionAgent(ProjectContext(tmp_path, {}))
    agent.role = "generator"
    agent.open(ClaudeAgentOptions())

    async def turn():
        async with agent:
            # A regression must fail fast, not hang the suite.
            return await asyncio.wait_for(agent.send("Implement the plan"), 10)

    assert asyncio.run(turn()) == "recovered"
    first, second = _HangingThenHealthyClient.instances
    assert first.query.await_args.args == ("Implement the plan",)
    # Replayed on a fresh session with the restart recap.
    replay = second.query.await_args.args[0]
    assert replay.endswith("Implement the plan")
    assert "replaced" in replay


def test_watch_stream_waits_while_the_cli_lives():
    async def slow():
        await asyncio.sleep(0.05)
        yield 1
        yield 2

    async def collect():
        return [item async for item in base._watch_stream(slow(), lambda: True, 0.01)]

    assert asyncio.run(collect()) == [1, 2]


def test_watch_stream_raises_when_the_cli_exited():
    async def silent():
        await asyncio.Event().wait()
        yield None

    async def consume():
        async for _item in base._watch_stream(silent(), lambda: False, 0.01):
            pass

    with pytest.raises(CLIConnectionError, match="exited mid-turn"):
        asyncio.run(consume())


def test_transport_alive_reads_the_process_returncode():
    def client(returncode):
        process = SimpleNamespace(returncode=returncode)
        return SimpleNamespace(_transport=SimpleNamespace(_process=process))

    assert base._transport_alive(client(None)) is True
    assert base._transport_alive(client(1)) is False
    assert base._transport_alive(SimpleNamespace()) is True  # unknown: alive
