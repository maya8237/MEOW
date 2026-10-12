"""Fetcher agents write JSON by hand; near-valid output must be repaired or
rejected with a clear message, never an unhandled parser error."""

import asyncio
from types import SimpleNamespace

import pytest

from meow.agents.mcp_fetcher import fetch_record

REQUIRED = ("key", "summary", "description")


def _fetch(text):
    fetcher = SimpleNamespace(server="jira", role="issue_fetcher")

    async def check_active():  # ruff: ignore[unused-async]
        return None

    fetcher.check_active = check_active

    async def write(path):  # ruff: ignore[unused-async]
        path.write_text(text, encoding="utf-8")

    return asyncio.run(fetch_record(fetcher, write, REQUIRED))


def test_markdown_fenced_json_is_accepted():
    data = _fetch('```json\n{"key": "A-1", "summary": "s", "description": "d"}\n```')
    assert data["key"] == "A-1"


def test_non_string_description_is_flattened_to_text():
    data = _fetch(
        '{"key": "A-1", "summary": "s", "description": ["step one", "step two"]}'
    )
    assert data["description"] == "step one\nstep two"


@pytest.mark.parametrize("text", ["[]", '"just prose"', "not json at all", ""])
def test_unusable_output_is_a_clear_runtime_error(text):
    with pytest.raises(RuntimeError, match="issue_fetcher"):
        _fetch(text)
