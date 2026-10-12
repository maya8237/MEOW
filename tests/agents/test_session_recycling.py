import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from meow.agents import base
from meow.agents.base import SessionAgent


def _client(*turns):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock()
    client.query = AsyncMock()

    async def stream(items):
        await asyncio.sleep(0)
        for item in items:
            yield item

    client.receive_response.side_effect = [stream(items) for items in turns]
    return client


def _text(text):
    return AssistantMessage(content=[TextBlock(text=text)], model="m")


def _result(tokens=0, *, error=None):
    return ResultMessage(
        subtype="error" if error else "success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=bool(error),
        num_turns=1,
        session_id="s",
        result=error,
        usage={"input_tokens": tokens},
    )


def _agent():
    agent = SessionAgent.__new__(SessionAgent)
    agent.role = "generator"
    return agent


class SessionRecyclingTests(unittest.IsolatedAsyncioTestCase):
    async def test_recycles_when_context_is_near_the_limit(self):
        first = _client([_text("one"), _result(base._SESSION_TOKEN_LIMIT + 1)])
        second = _client([_text("two"), _result(10)])
        agent = _agent()
        with patch("meow.agents.base.ClaudeSDKClient", side_effect=[first, second]):
            agent.open(MagicMock(resume="old", continue_conversation=True))
            async with agent:
                self.assertEqual(await agent.send("a"), "one")
                self.assertEqual(await agent.send("b"), "two")
        first.__aexit__.assert_awaited()
        sent = second.query.await_args.args[0]
        self.assertIn("context limit", sent)
        self.assertTrue(sent.endswith("b"))

    async def test_context_overflow_error_retries_once_on_a_fresh_session(self):
        first = _client([_result(error="Prompt is too long")])
        second = _client([_text("done"), _result(10)])
        agent = _agent()
        with patch("meow.agents.base.ClaudeSDKClient", side_effect=[first, second]):
            agent.open(MagicMock())
            async with agent:
                self.assertEqual(await agent.send("go"), "done")

    async def test_repeated_overflow_raises(self):
        clients = [_client([_result(error="prompt is too long")]) for _ in range(2)]
        agent = _agent()
        with patch("meow.agents.base.ClaudeSDKClient", side_effect=clients):
            agent.open(MagicMock())
            async with agent:
                with self.assertRaisesRegex(RuntimeError, "context window"):
                    await agent.send("go")
