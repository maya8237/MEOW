import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage

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
