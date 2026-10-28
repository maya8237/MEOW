"""Claude API failures (direct or through LiteLLM): classification and policy.

`classify_api_error` maps the SDK's structured signals and error text to an
`ApiFailure`; `ApiRetries` is the per-turn policy that waits, backs off,
restarts, parks (`TransientRunError`) or blocks (`BlockingRunError`).
"""

import asyncio
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from claude_agent_sdk import AssistantMessage, RateLimitEvent, ResultMessage

from meow.execution.run_failures import BlockingRunError, TransientRunError
from meow.infrastructure.logging import get_logger

logger = get_logger(__name__)

Kind = Literal["wait", "backoff", "restart", "sequence", "overflow", "park", "blocking"]

DEFAULT_MAX_WAIT = 10800.0
_WAIT_BASE, _WAIT_CAP = 60.0, 900.0
_BACKOFF_BASE, _BACKOFF_CAP, _BACKOFF_TRIES = 15.0, 300.0, 6
_BLOCKING_DELAY = 30.0
_PARK_DEFAULT = 3600.0

_STATUS = re.compile(r"API Error: (\d{3})")
_LITELLM = re.compile(r"litellm\.(?:exceptions\.)?(\w+)")
_SEQUENCE = re.compile(
    r"unexpected role ['\"`]?user['\"`]? after role ['\"`]?tool|"
    r"roles must alternate|tool_use ids were found without .?tool_result|"
    r"unexpected .?tool_use_id",
    re.I,
)
_MODEL_KEY = re.compile(
    r"(?i)(model[^\n]{0,80}(rate|rpm|tpm|limit|budget)[^\n]{0,80}key|"
    r"key[^\n]{0,80}model[^\n]{0,80}(rate|rpm|tpm|limit|budget)|"
    r"model_max_budget|model.?per.?key|Max parallel request limit|"
    r"over (the )?(model )?(rpm|tpm)? ?limit for model)"
)
_MODEL_AND_KEY = re.compile(r"(?i)\bmodel\b.*\b(api_key|user_api_key|key)\b")
_CONTEXT = re.compile(
    r"prompt is too long|context (?:window|length|limit)|too many tokens|"
    r"maximum context|exceed(?:s|ed)? .{0,30}context",
    re.I,
)
_RETRY_AFTER = re.compile(
    r"(?:retry[ -]?after|x-ratelimit-reset-\w+|try again in)\W{0,3}(\d+(?:\.\d+)?)"
    r"\s*(ms|milliseconds?|s\b|sec\w*)?",
    re.I,
)
_BUDGET_TYPE = re.compile(r"[\"']type[\"']\s*:\s*[\"']budget_exceeded[\"']")
_KEY = re.compile(r"(?i)\bkey\W{0,6}(sk-[\w-]+|[0-9a-f]{32,64})")
_TOO_MANY, _BAD_REQUEST, _TOO_LARGE, _SERVER = 429, 400, 413, 500
_REDACTED = "[redacted-key]"
_MODEL = re.compile(r"(?i)\bmodel[=:\s'\"]+([\w./:-]+)")
_MODEL_STOP = frozenset({"rate", "per", "limit", "max", "budget", "not", "key"})

_BY_CLASS: dict[str, tuple[Kind, str]] = {}
for _names, _kind, _cause in (
    (("RateLimitError",), "wait", "rate_limit"),
    (
        (
            "InternalServerError",
            "ServiceUnavailableError",
            "APIError",
            "BadGatewayError",
        ),
        "backoff",
        "server_error",
    ),
    (("Timeout", "APIConnectionError"), "backoff", "api_timeout"),
    (("ContextWindowExceededError",), "overflow", "context"),
    (
        ("BadRequestError", "UnsupportedParamsError", "UnprocessableEntityError"),
        "restart",
        "invalid_request",
    ),
    (("ContentPolicyViolationError",), "restart", "content_policy"),
    (("BudgetExceededError",), "park", "budget"),
    (("AuthenticationError", "PermissionDeniedError"), "blocking", "auth"),
    (("NotFoundError",), "blocking", "model_unavailable"),
):
    for _name in _names:
        _BY_CLASS[_name] = (_kind, _cause)  # type: ignore[assignment]

_BY_STATUS: dict[int, tuple[Kind, str]] = {
    **dict.fromkeys((500, 502, 503, 504, 529), ("backoff", "server_error")),
    408: ("backoff", "api_timeout"),
    413: ("overflow", "context"),
    429: ("wait", "rate_limit"),
    402: ("park", "budget"),
    401: ("blocking", "auth"),
    403: ("blocking", "auth"),
    404: ("blocking", "model_unavailable"),
    400: ("restart", "invalid_request"),
}
_BY_TEXT: tuple[tuple[str, Kind, str], ...] = (
    (r"rate_limit", "wait", "rate_limit"),
    (r"server_error|overloaded", "backoff", "server_error"),
    (r"ECONNRESET|Connection error|MidStreamFallbackError", "backoff", "api_timeout"),
    (r"invalid_request", "restart", "invalid_request"),
    (r"refusal|content[ _]policy", "restart", "content_policy"),
    (r"billing_error", "park", "budget"),
    (r"authentication_failed|authentication_error", "blocking", "auth"),
    (r"model .* not found", "blocking", "model_unavailable"),
)


@dataclass(frozen=True)
class ApiFailure:
    kind: Kind
    cause: str
    retry_after: float | None = None
    status: int | None = None


def redact(text: str) -> str:
    """`text` with anything key-like replaced; error bodies reach exception
    messages, run records and stderr, so nothing there may carry a key."""
    text = _KEY.sub(lambda m: m.group(0).replace(m.group(1), _REDACTED), text)
    text = re.sub(r"\bsk-[\w-]{8,}", _REDACTED, text)
    return re.sub(r"(?i)(Bearer )\S+", rf"\1{_REDACTED}", text)


def _hint_seconds(text: str) -> float | None:
    """A reset the error body reports (Retry-After, x-ratelimit-reset-*)."""
    found = _RETRY_AFTER.search(text)
    if not found:
        return None
    seconds = float(found.group(1))
    unit = (found.group(2) or "").lower()
    return seconds / 1000 if unit.startswith("m") else seconds


def _status_of(status: int | None, text: str) -> int | None:
    if status is not None:
        return status
    found = _STATUS.search(text)
    return int(found.group(1)) if found else None


def classify_api_error(  # ruff: ignore[complex-structure, too-many-return-statements, too-many-branches]
    *, status: int | None, error: str | None, text: str
) -> ApiFailure | None:
    """The `ApiFailure` a result/assistant error or error text describes, or
    None. `status` is `ResultMessage.api_error_status`, `error` is
    `AssistantMessage.error`, `text` the result, stderr or last assistant text."""
    text = text or ""
    code = _status_of(status, text)
    classes = _LITELLM.findall(text)
    rate_limited = code == _TOO_MANY or "RateLimitError" in classes
    hint = _hint_seconds(text)

    def make(kind: Kind, cause: str) -> ApiFailure:
        return ApiFailure(kind, cause, hint if kind == "wait" else None, code)

    if (code in {None, _TOO_MANY} or rate_limited) and (
        _MODEL_KEY.search(text) or (rate_limited and _MODEL_AND_KEY.search(text))
    ):
        return make("wait", "model_key_rate_limit")
    if code == _BAD_REQUEST and _SEQUENCE.search(text):
        return make("sequence", "invalid_sequence")
    if (
        code == _TOO_LARGE
        or "ContextWindowExceededError" in classes
        or _CONTEXT.search(text)
    ):
        return make("overflow", "context")
    # A specific LiteLLM class (or error type) outranks the generic status it
    # travels under: a budget error arrives as a 400, a policy block as a 400.
    if "BudgetExceededError" in classes or _BUDGET_TYPE.search(text):
        return make("park", "budget")
    if "ContentPolicyViolationError" in classes:
        return make("restart", "content_policy")
    if code in _BY_STATUS:
        return make(*_BY_STATUS[code])
    if code is not None and _SERVER <= code < _SERVER + 100:
        return make("backoff", "server_error")
    for name in classes:
        if name in _BY_CLASS:
            return make(*_BY_CLASS[name])
    for signal in (error or "", text):
        for pattern, kind, cause in _BY_TEXT:
            if re.search(pattern, signal, re.I):
                return make(kind, cause)
    return None


def api_error_from(message: object) -> ApiFailure | None:
    """The failure an SDK `ResultMessage` or `AssistantMessage` reports."""
    if isinstance(message, ResultMessage):
        failed = message.is_error or message.subtype != "success"
        if not failed:
            return None
        return classify_api_error(
            status=message.api_error_status, error=None, text=message.result or ""
        )
    if isinstance(message, AssistantMessage) and message.error:
        text = " ".join(getattr(block, "text", "") for block in message.content).strip()
        return classify_api_error(status=None, error=str(message.error), text=text)
    return None


class ApiError(Exception):
    """A classified API failure raised out of a stream, for the retry loops."""

    def __init__(self, failure: ApiFailure, text: str = "") -> None:
        self.ids = _safe_ids(text, None)  # before redaction: key prefix for logs
        text = redact(text)
        super().__init__(f"{failure.cause}: {text[:200]}")
        self.failure = failure
        self.text = text


def _sleep_seconds(attempt: int) -> float:
    return min(_WAIT_BASE * 2**attempt, _WAIT_CAP)


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def _safe_ids(text: str, model: str | None) -> dict[str, str]:
    """The model and a truncated key id for the log; never a full key."""
    key = _KEY.search(text)
    found = model or next(
        (m for m in _MODEL.findall(text) if m.lower() not in _MODEL_STOP), ""
    )
    return {"model": found, "key_id": key.group(1)[:7] if key else ""}


class ApiRetries:
    """Per-turn API retry policy. `handle` returns True to resend the same
    message in the same session, False to restart on a fresh session, or
    raises `TransientRunError` (park) / `BlockingRunError`."""

    def __init__(
        self,
        role: str,
        max_wait: float | Callable[[], float] = DEFAULT_MAX_WAIT,
        *,
        model: str | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.role = role
        self._max_wait = max_wait
        self.model = model
        self._now = now or time.time
        self.resets_at: float | None = None
        self.waited = 0.0
        self._counts: dict[str, int] = {}
        self._last_reset: float | None = None

    def note(self, message: object) -> None:
        """Remember the latest rejected `RateLimitEvent` reset time."""
        if isinstance(message, RateLimitEvent):
            info = message.rate_limit_info
            if info.status == "rejected" and info.resets_at:
                self.resets_at = float(info.resets_at)

    def _count(self, cause: str) -> int:
        n = self._counts.get(cause, 0)
        self._counts[cause] = n + 1
        return n

    def _park(self, failure: ApiFailure, retry_after: float | None, text: str):
        raise TransientRunError(
            f"{self.role} API error ({failure.cause}) persisted: {text[:200]}",
            cause=failure.cause,
            retry_after=retry_after,
        )

    async def handle(  # ruff: ignore[complex-structure, too-many-branches, too-many-statements]
        self, error: ApiError
    ) -> bool:
        failure, text = error.failure, error.text
        cause = failure.cause
        ids = {**error.ids, **({"model": self.model} if self.model else {})}
        if failure.kind == "wait":
            return await self._wait(failure, text, ids)
        n = self._count(cause)
        if failure.kind == "backoff":
            if n >= _BACKOFF_TRIES:
                self._park(failure, None, text)
            delay = min(_BACKOFF_BASE * 2**n, _BACKOFF_CAP)
            await self._pause("api_backoff", cause, delay, n, ids)
            return True
        if failure.kind == "restart":
            if n >= 1:
                self._park(failure, None, text)
            logger.warning("api_restart", role=self.role, cause=cause, **ids)
            return False
        if failure.kind == "park":
            self._park(failure, _PARK_DEFAULT, text)
        if failure.kind == "blocking":
            if n >= 1:
                raise BlockingRunError(
                    f"{self.role} API error ({cause}): {text[:200]}", cause=cause
                )
            await self._pause("api_blocking_retry", cause, _BLOCKING_DELAY, n, ids)
            return True
        raise error

    async def _wait(self, failure: ApiFailure, text: str, ids: dict) -> bool:
        cause = failure.cause
        n = self._count(cause)
        max_wait = self._max_wait() if callable(self._max_wait) else self._max_wait
        reset = None
        if cause == "rate_limit" and self.resets_at:
            reset = self.resets_at - self._now()
        reported = True
        if reset is not None and reset > 0:
            delay = reset
        elif failure.retry_after is not None:
            delay = failure.retry_after
        else:
            delay, reported = _sleep_seconds(n), False
        if failure.retry_after is not None:
            self._last_reset = failure.retry_after
        if self.waited >= max_wait or (reported and delay > max_wait):
            hint = self._last_reset or (
                delay if reported and delay > max_wait else None
            )
            self._park(failure, hint or _PARK_DEFAULT, text)
        self.waited += delay
        await self._pause("api_wait", cause, delay, n, ids)
        return True

    async def _pause(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
        self, event: str, cause: str, delay: float, attempt: int, ids: dict
    ) -> None:
        logger.warning(
            event, role=self.role, cause=cause, seconds=delay, attempt=attempt, **ids
        )
        await _sleep(delay)
