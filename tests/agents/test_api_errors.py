import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    RateLimitEvent,
    RateLimitInfo,
    ResultMessage,
    TextBlock,
)

from meow.agents import api_errors, base
from meow.agents.api_errors import ApiFailure, api_error_from, classify_api_error
from meow.agents.base import Agent, ProjectContext, SessionAgent
from meow.execution.run_failures import BlockingRunError, TransientRunError

TWICE = 2
REPORTED_RESET = 45.0
KEY = "sk-abc123def456ghi789"
MODEL_KEY_BODY = (
    f"API Error: 429 litellm.RateLimitError: Model rate limit exceeded for key "
    f"{KEY} model gpt-x: 60 rpm"
)
SEQ = "API Error: 400 litellm.BadRequestError: roles must alternate"

TABLE = [
    (429, "rate_limit", "API Error: 429 rate limited", "wait", "rate_limit"),
    (None, None, "litellm.RateLimitError: slow down", "wait", "rate_limit"),
    (None, "rate_limit", "", "wait", "rate_limit"),
    (None, None, MODEL_KEY_BODY, "wait", "model_key_rate_limit"),
    (
        None,
        None,
        "litellm.RateLimitError Max parallel request limit reached",
        "wait",
        "model_key_rate_limit",
    ),
    (429, None, "model_max_budget exceeded", "wait", "model_key_rate_limit"),
    (529, None, "", "backoff", "server_error"),
    (503, None, "", "backoff", "server_error"),
    (None, "server_error", "", "backoff", "server_error"),
    (None, None, "API Error: Overloaded", "backoff", "server_error"),
    (None, None, "litellm.ServiceUnavailableError: down", "backoff", "server_error"),
    (408, None, "", "backoff", "api_timeout"),
    (None, None, "litellm.APIConnectionError: x", "backoff", "api_timeout"),
    (None, None, "read ECONNRESET", "backoff", "api_timeout"),
    (None, None, "Connection error.", "backoff", "api_timeout"),
    (413, None, "", "overflow", "context"),
    (None, None, "Prompt is too long", "overflow", "context"),
    (None, None, "litellm.ContextWindowExceededError: x", "overflow", "context"),
    (400, None, SEQ, "sequence", "invalid_sequence"),
    (400, None, "bad field", "restart", "invalid_request"),
    (None, "invalid_request", "", "restart", "invalid_request"),
    (None, None, "litellm.UnsupportedParamsError: x", "restart", "invalid_request"),
    (None, None, "litellm.ContentPolicyViolationError: x", "restart", "content_policy"),
    (402, None, "", "park", "budget"),
    (None, "billing_error", "", "park", "budget"),
    (None, None, "litellm.BudgetExceededError: x", "park", "budget"),
    (401, None, "", "blocking", "auth"),
    (403, None, "", "blocking", "auth"),
    (None, "authentication_failed", "", "blocking", "auth"),
    (None, None, "litellm.PermissionDeniedError: x", "blocking", "auth"),
    (404, None, "", "blocking", "model_unavailable"),
    (None, None, "litellm.NotFoundError: x", "blocking", "model_unavailable"),
    (None, None, "model foo-1 not found", "blocking", "model_unavailable"),
]


@pytest.mark.parametrize(("status", "error", "text", "kind", "cause"), TABLE)
def test_classify_api_error_table(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
    status, error, text, kind, cause
):
    failure = classify_api_error(status=status, error=error, text=text)
    assert (failure.kind, failure.cause) == (kind, cause)


def test_classify_returns_none_when_nothing_matches():
    assert classify_api_error(status=None, error=None, text="all good") is None


def test_retry_after_is_read_from_the_body():
    failure = classify_api_error(status=429, error=None, text="retry after 45 seconds")
    assert failure == ApiFailure("wait", "rate_limit", 45.0, 429)


def test_model_per_key_429_is_detected():
    texts = [
        MODEL_KEY_BODY,
        "429 Max parallel request limit reached",
        "429 model_max_budget limit hit",
        "429 litellm.RateLimitError: model=gpt-x api_key=abc123 limit",
    ]
    for text in texts:
        failure = classify_api_error(status=None, error=None, text=text)
        assert (failure.kind, failure.cause) == ("wait", "model_key_rate_limit")
    plain = classify_api_error(
        status=429, error=None, text="rate_limit_error: too many requests"
    )
    assert plain.cause == "rate_limit"


def test_api_error_from_reads_sdk_messages():
    assert api_error_from(_result(status=529)).cause == "server_error"
    assert api_error_from(_assistant("oops", "rate_limit")).cause == "rate_limit"
    assert api_error_from(_ok()) is None


def _result(text="", status=None):
    return ResultMessage(
        subtype="success" if status is None and not text else "error_during_execution",
        duration_ms=0,
        duration_api_ms=0,
        is_error=bool(text) or status is not None,
        num_turns=1,
        session_id="s",
        result=text or None,
        api_error_status=status,
    )


def _ok():
    return ResultMessage(
        subtype="success",
        duration_ms=0,
        duration_api_ms=0,
        is_error=False,
        num_turns=1,
        session_id="s",
        result="done",
    )


def _assistant(text, error=None):
    return AssistantMessage(content=[TextBlock(text=text)], model="m", error=error)


def _event(resets_at, status="rejected"):
    info = RateLimitInfo(status=status, resets_at=resets_at)
    return RateLimitEvent(rate_limit_info=info, uuid="u", session_id="s")


@pytest.fixture
def sleeps(monkeypatch):
    log = []

    async def fake(seconds):  # ruff: ignore[unused-async] -- awaited stub
        log.append(seconds)

    monkeypatch.setattr(api_errors, "_sleep", fake)
    return log


def _scripted(monkeypatch, scripts):
    calls = []

    def fake_query(*, prompt, options):
        calls.append((prompt, options))
        script = scripts[min(len(calls), len(scripts)) - 1]

        async def stream():  # ruff: ignore[unused-async] -- SDK async stream
            for item in script:
                yield item

        return stream()

    monkeypatch.setattr(base, "query", fake_query)
    return calls


def _run(options=None):
    return asyncio.run(
        Agent.run_query("do it", options or ClaudeAgentOptions(model="gpt-x"), "gen")
    )


def test_rate_limit_waits_for_reset(monkeypatch, sleeps):
    monkeypatch.setattr("time.time", lambda: 1000.0)
    failing = [_event(1120), _result(status=429, text="API Error: 429")]
    calls = _scripted(monkeypatch, [failing, [_assistant("fine"), _ok()]])
    assert _run() == "fine"
    assert sleeps == [120.0]
    assert calls[0][0] == calls[1][0] == "do it"
    assert calls[0][1] is calls[1][1]


def test_rate_limit_parks_after_api_max_wait(monkeypatch, sleeps):
    monkeypatch.setattr(base, "_max_wait", lambda *_: 100.0)
    _scripted(monkeypatch, [[_result(status=429, text="API Error: 429")]])
    with pytest.raises(TransientRunError) as caught:
        _run()
    assert caught.value.cause == "rate_limit"
    assert sleeps == [60.0, 120.0]


def test_model_per_key_429_waits_reported_reset(monkeypatch, sleeps):
    body = "API Error: 429 Max parallel request limit reached, retry after 45 seconds"
    calls = _scripted(monkeypatch, [[_result(text=body, status=429)], [_ok()]])
    _run()
    assert sleeps == [45.0]
    assert len(calls) == TWICE


def test_model_per_key_429_parks_with_retry_after_and_never_logs_key(
    monkeypatch, sleeps, capsys
):
    monkeypatch.setattr(base, "_max_wait", lambda *_: 50.0)
    body = MODEL_KEY_BODY + " retry after 45 seconds"
    _scripted(monkeypatch, [[_result(text=body, status=429)]])
    with pytest.raises(TransientRunError) as caught:
        _run()
    out = capsys.readouterr().out
    assert caught.value.cause == "model_key_rate_limit"
    assert caught.value.retry_after == pytest.approx(REPORTED_RESET)
    assert KEY not in out
    assert "sk-abc1" in out
    assert "gpt-x" in out


def test_overloaded_529_backs_off_then_parks(monkeypatch, sleeps):
    _scripted(monkeypatch, [[_result(status=529, text="API Error: 529")]])
    with pytest.raises(TransientRunError) as caught:
        _run()
    assert caught.value.cause == "server_error"
    assert sleeps == [15, 30, 60, 120, 240, 300]


def test_budget_parks_with_retry_after(monkeypatch, sleeps):
    _scripted(monkeypatch, [[_result(status=402, text="API Error: 402")]])
    with pytest.raises(TransientRunError) as caught:
        _run()
    assert (caught.value.cause, caught.value.retry_after) == ("budget", 3600.0)
    assert sleeps == []


def test_auth_retries_once_then_blocks(monkeypatch, sleeps):
    calls = _scripted(monkeypatch, [[_result(status=401, text="API Error: 401")]])
    with pytest.raises(BlockingRunError) as caught:
        _run()
    assert caught.value.cause == "auth"
    assert sleeps == [30.0]
    assert len(calls) == TWICE


def test_invalid_request_restarts_fresh_once_then_parks(monkeypatch, sleeps):
    calls = _scripted(monkeypatch, [[_result(status=400, text="API Error: 400 bad")]])
    options = ClaudeAgentOptions(resume="old")
    with pytest.raises(TransientRunError) as caught:
        _run(options)
    assert caught.value.cause == "invalid_request"
    assert len(calls) == TWICE
    assert calls[1][1].resume is None
    assert calls[1][0].endswith("do it")


def test_session_waits_in_the_same_session(monkeypatch, sleeps, tmp_path):
    streams = iter([
        [
            _result(status=529, text="API Error: 529"),
        ],
        [_assistant("ok"), _ok()],
    ])
    client = MagicMock()
    client.query = AsyncMock()
    client.receive_response = lambda: _aiter(next(streams))
    made = []
    monkeypatch.setattr(base, "ClaudeSDKClient", lambda **_: made.append(1) or client)
    agent = SessionAgent(ProjectContext(tmp_path, {}))
    agent.role = "generator"
    agent.open(ClaudeAgentOptions())
    assert asyncio.run(agent.send("go")) == "ok"
    assert sleeps == [15]
    assert made == [1]


async def _aiter(items):  # ruff: ignore[unused-async] -- SDK async stream
    for item in items:
        yield item


def test_sdk_env_sets_retry_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_MAX_RETRIES", raising=False)
    monkeypatch.setenv("API_TIMEOUT_MS", "1")
    options = Agent(ProjectContext(tmp_path, {"models": {}})).options(
        system_prompt="x", allowed_tools=[], role="generator"
    )
    assert options.env["CLAUDE_CODE_MAX_RETRIES"] == "8"
    assert options.env["API_TIMEOUT_MS"] == "1"


def test_parked_error_message_never_contains_the_key(monkeypatch, sleeps):
    monkeypatch.setattr(base, "_max_wait", lambda *_: 1.0)
    body = MODEL_KEY_BODY + " retry after 45 seconds"
    _scripted(monkeypatch, [[_result(text=body, status=429)]])
    with pytest.raises(TransientRunError) as caught:
        _run()
    assert KEY not in str(caught.value)
    assert "sk-abc1" not in str(caught.value).replace("[redacted-key]", "")


def test_blocking_error_message_never_contains_the_key(monkeypatch, sleeps):
    body = f"API Error: 401 invalid key {KEY}"
    _scripted(monkeypatch, [[_result(text=body, status=401)]])
    with pytest.raises(BlockingRunError) as caught:
        _run()
    assert KEY not in str(caught.value)


def test_apierror_and_failed_turn_messages_redact_the_key():
    failure = ApiFailure("backoff", "server_error")
    assert KEY not in str(api_errors.ApiError(failure, f"key {KEY} broke"))
    assert KEY not in api_errors.redact(f"Bearer {KEY} and key {KEY}")


def test_session_failure_message_redacts_the_key(tmp_path):
    agent = SessionAgent(ProjectContext(tmp_path, {}))
    agent.role = "generator"
    result = _result(text=f"max_turns hit using key {KEY}")
    with pytest.raises(RuntimeError) as caught:
        agent._check_result(result)
    assert KEY not in str(caught.value)


@pytest.mark.parametrize("prefix", ["", "litellm.BadRequestError: "])
def test_structured_400_sequence_error_rebuilds(monkeypatch, sleeps, prefix):
    bad = _result(text=f"{prefix}roles must alternate", status=400)
    calls = _scripted(monkeypatch, [[bad], [_assistant("fine"), _ok()]])
    assert _run() == "fine"
    assert len(calls) == TWICE
    assert calls[1][0].endswith("do it")
    assert calls[1][0] != "do it"


def test_session_structured_400_sequence_error_restarts(monkeypatch, sleeps, tmp_path):
    streams = iter([
        [_result(text="roles must alternate", status=400)],
        [_assistant("ok"), _ok()],
    ])
    client = MagicMock()
    client.query = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.receive_response = lambda: _aiter(next(streams))
    made = []
    monkeypatch.setattr(base, "ClaudeSDKClient", lambda **_: made.append(1) or client)
    agent = SessionAgent(ProjectContext(tmp_path, {}))
    agent.role = "generator"
    agent.open(ClaudeAgentOptions())

    async def go():
        async with agent:
            return await agent.send("go")

    assert asyncio.run(go()) == "ok"
    assert len(made) == TWICE


@pytest.mark.parametrize(
    "prose",
    [
        "I added a fallback for overloaded servers",
        "model foo rate limit per key, press any key to continue",
    ],
)
def test_assistant_prose_is_not_classified(monkeypatch, sleeps, prose):
    failing = [_assistant(prose), _result(text="", status=None)]
    failing[1] = ResultMessage(
        subtype="error_max_turns",
        duration_ms=0,
        duration_api_ms=0,
        is_error=True,
        num_turns=1,
        session_id="s",
        result=None,
    )
    _scripted(monkeypatch, [failing])
    with pytest.raises(RuntimeError) as caught:
        _run()
    assert not isinstance(caught.value, TransientRunError)
    assert sleeps == []


def test_assistant_api_error_notice_is_classified(monkeypatch, sleeps):
    notice = "API Error: 529 Overloaded"
    failing = ResultMessage(
        subtype="error_during_execution",
        duration_ms=0,
        duration_api_ms=0,
        is_error=True,
        num_turns=1,
        session_id="s",
        result=None,
    )
    _scripted(monkeypatch, [[_assistant(notice), failing], [_ok()]])
    _run()
    assert sleeps == [15]


@pytest.mark.parametrize("status", [500, 501, 520, 524, 599])
def test_any_5xx_is_a_server_error(status):
    failure = classify_api_error(status=status, error=None, text="")
    assert (failure.kind, failure.cause) == ("backoff", "server_error")


def test_litellm_exceptions_module_prefix_is_read():
    failure = classify_api_error(
        status=None, error=None, text="litellm.exceptions.RateLimitError: slow"
    )
    assert failure.cause == "rate_limit"


def test_restart_recap_is_generic_and_does_not_stack(monkeypatch, sleeps):
    first = _result(status=400, text="API Error: 400 bad")
    second = _result(
        status=None, text="API Error: litellm.ContentPolicyViolationError: no"
    )
    calls = _scripted(monkeypatch, [[first], [second], [_ok()]])
    _run()
    prompt = calls[2][0]
    assert prompt.endswith("do it")
    assert "working tree" not in prompt
    assert prompt.count("start over") == 1
