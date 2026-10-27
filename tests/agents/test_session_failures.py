import asyncio
from contextlib import aclosing
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

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


def test_watch_stream_closes_the_inner_iterator_on_early_exit():
    closed = []

    async def source():  # ruff: ignore[unused-async] -- SDK async stream
        try:
            for i in range(5):
                yield i
        finally:
            closed.append(True)

    async def consume():
        watched = base._watch_stream(source(), lambda: True, 0.01)
        async with aclosing(watched):
            async for _item in watched:
                break
        return list(closed)  # before asyncio.run's own generator cleanup

    assert asyncio.run(consume()) == [True]


def test_watch_stream_closes_the_inner_iterator_when_the_cli_died():
    closed = []

    async def silent():
        try:
            await asyncio.Event().wait()
            yield None
        finally:
            closed.append(True)

    async def consume():
        try:
            async for _item in base._watch_stream(silent(), lambda: False, 0.01):
                pass
        except CLIConnectionError:
            return list(closed)

    assert asyncio.run(consume()) == [True]


_SEQUENCE_400 = (
    "API Error: 400 litellm.BadRequestError: unexpected role 'user' after role 'tool'"
)


def _query_result(error=None):
    from claude_agent_sdk import ResultMessage

    return ResultMessage(
        subtype="error" if error else "success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=bool(error),
        num_turns=1,
        session_id="s",
        result=error,
    )


def test_run_query_retries_a_sequence_error_without_resume():
    prompts = []
    resumes = []

    def fake_query(*, prompt, options):
        prompts.append(prompt)
        resumes.append(options.resume)

        async def gen():  # ruff: ignore[unused-async] -- SDK async stream
            yield _query_result(_SEQUENCE_400 if len(prompts) == 1 else None)

        return gen()

    options = SimpleNamespace(resume="old", continue_conversation=True)
    with patch("meow.agents.base.query", fake_query):
        asyncio.run(base.Agent.run_query("do it", options, "reviewer"))
    assert resumes == ["old", None]
    assert "invalid message history" in prompts[1]
    assert prompts[1].endswith("do it")
    assert options.resume == "old"


def test_run_query_parks_after_a_second_sequence_error():
    from meow.execution.run_failures import TransientRunError

    def fake_query(*, prompt, options):
        async def gen():  # ruff: ignore[unused-async] -- SDK async stream
            yield _query_result(_SEQUENCE_400)

        return gen()

    with (
        patch("meow.agents.base.query", fake_query),
        pytest.raises(TransientRunError) as caught,
    ):
        asyncio.run(base.Agent.run_query("x", SimpleNamespace(resume="o"), "reviewer"))
    assert caught.value.cause == "invalid_sequence"
