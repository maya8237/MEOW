import asyncio

import pytest
from claude_agent_sdk import ClaudeAgentOptions, ProcessError, ResultMessage

from meow.agents.api_errors import is_context_overflow
from meow.agents.base import Agent


def _result(error=None, status=None):
    return ResultMessage(
        subtype="error_during_execution" if error else "success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=bool(error),
        num_turns=1,
        session_id="fresh",
        result=error or "done",
        api_error_status=status,
    )


def _run(monkeypatch, failure):
    calls = []

    async def fake_query(*, prompt, options):
        await asyncio.sleep(0)
        calls.append((prompt, options.resume))
        if options.resume:
            if isinstance(failure, Exception):
                raise failure
            yield failure
        else:
            yield _result()

    monkeypatch.setattr("meow.agents.base.query", fake_query)
    return calls


@pytest.mark.parametrize(
    "text",
    [
        "Prompt is too long",
        "litellm.ContextWindowExceededError: too big",
        "context_length_exceeded",
    ],
)
def test_overflow_detector_matches_provider_wordings(text):
    assert is_context_overflow(text)
    assert not is_context_overflow("rate limit reached")


@pytest.mark.parametrize(
    "failure",
    [
        _result("litellm.ContextWindowExceededError: input too large"),
        _result("boom", status=413),
        ProcessError(
            "failed", exit_code=1, stderr="API Error: ContextWindowExceededError"
        ),
    ],
)
def test_resumed_session_overflow_restarts_fresh(monkeypatch, failure):
    calls = _run(monkeypatch, failure)
    options = ClaudeAgentOptions(resume="old-session")
    asyncio.run(Agent.run_query("task", options, "Reviewer"))
    assert [resume for _, resume in calls] == ["old-session", None]
    assert "full context window" in calls[1][0]


def test_fresh_session_overflow_gives_up_clearly(monkeypatch):
    async def fake_query(*, prompt, options):
        await asyncio.sleep(0)
        yield _result("prompt is too long")

    monkeypatch.setattr("meow.agents.base.query", fake_query)
    with pytest.raises(RuntimeError, match="even on a fresh session"):
        asyncio.run(Agent.run_query("task", ClaudeAgentOptions(), "Reviewer"))
