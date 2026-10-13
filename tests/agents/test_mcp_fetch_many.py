"""JQL searches return a list of issues, which the record fetcher would flatten."""

import asyncio
from types import SimpleNamespace

import pytest

from meow.agents.base import ProjectContext
from meow.agents.mcp_fetcher import IssueFetcherAgent, fetch_issue_list, fetch_record


class StubFetcher:
    server = "jira"
    role = "issue_fetcher"

    def __init__(self, text: str):
        self.text = text
        self.checks = 0
        self.queries: list[str] = []

    async def check_active(self):
        self.checks += 1

    async def fetch_many(self, jql, output_file):
        self.queries.append(jql)
        output_file.write_text(self.text, encoding="utf-8")


def issues_from(text: str) -> list[dict]:
    return asyncio.run(fetch_issue_list(StubFetcher(text), "project = PROJ"))


def test_fetch_issue_list_parses_issue_objects():
    fetcher = StubFetcher(
        '{"issues": [{"key": "PROJ-1", "summary": "Login", '
        '"description": ["step one", "step two"]}, '
        '{"key": "PROJ-2", "summary": "Logout", "description": null}]}'
    )

    issues = asyncio.run(fetch_issue_list(fetcher, "project = PROJ"))

    assert issues == [
        {"key": "PROJ-1", "summary": "Login", "description": "step one\nstep two"},
        {"key": "PROJ-2", "summary": "Logout", "description": ""},
    ]
    assert (fetcher.checks, fetcher.queries) == (1, ["project = PROJ"])


def test_fetch_issue_list_accepts_a_fenced_bare_list():
    text = '```json\n[{"key": "PROJ-1", "summary": "Login", "description": "d"}]\n```'

    assert issues_from(text) == [
        {"key": "PROJ-1", "summary": "Login", "description": "d"}
    ]


def test_fetch_issue_list_allows_no_matches():
    assert issues_from('{"issues": []}') == []


@pytest.mark.parametrize(
    "text", ['{"issues": "PROJ-1"}', '{"issues": {"a": 1}}', '{"other": []}', '"x"']
)
def test_fetch_issue_list_rejects_non_list(text):
    with pytest.raises(
        RuntimeError, match="issue_fetcher output must hold an 'issues'"
    ):
        issues_from(text)


def test_fetch_issue_list_rejects_issue_without_key_or_summary():
    with pytest.raises(RuntimeError, match="needs a key and a summary"):
        issues_from('{"issues": [{"summary": "no key"}]}')


def test_fetch_issue_list_rejects_non_object_issue():
    with pytest.raises(RuntimeError, match="issue must be a JSON object"):
        issues_from('{"issues": ["PROJ-1"]}')


def test_fetch_issue_list_rejects_invalid_json():
    with pytest.raises(RuntimeError, match="issue_fetcher output is not valid JSON"):
        issues_from("not json")


def test_fetch_record_skips_the_preflight_on_request():
    fetcher = SimpleNamespace(server="jira", role="issue_fetcher")

    async def check_active():  # ruff: ignore[unused-async]
        raise AssertionError("preflight must be skipped")

    async def write(path):  # ruff: ignore[unused-async]
        path.write_text('{"key": "A-1", "summary": "s", "description": "d"}')

    fetcher.check_active = check_active

    data = asyncio.run(
        fetch_record(fetcher, write, ("key", "summary", "description"), preflight=False)
    )

    assert data["key"] == "A-1"


def agent(tmp_path) -> IssueFetcherAgent:
    context = ProjectContext(tmp_path, {"models": {"issue_fetcher": None}})
    return IssueFetcherAgent(context, {"command": "uvx", "args": []})


def test_fetch_many_asks_for_an_issues_array(tmp_path, monkeypatch):
    output_file = tmp_path / "issues.json"
    captured = {}

    async def fake_run_query(prompt, options, role):  # ruff: ignore[unused-async]
        captured.update(prompt=prompt, options=options)
        output_file.write_text('{"issues": []}', encoding="utf-8")

    monkeypatch.setattr(IssueFetcherAgent, "run_query", staticmethod(fake_run_query))

    asyncio.run(agent(tmp_path).fetch_many("project = PROJ", output_file))

    system_prompt = captured["options"].system_prompt
    assert "project = PROJ" in system_prompt
    assert str(output_file) in system_prompt
    assert '"issues"' in system_prompt
    assert "mcp__jira__*" in captured["options"].allowed_tools


def test_fetch_many_fails_when_nothing_was_written(tmp_path, monkeypatch):
    async def fake_run_query(prompt, options, role):  # ruff: ignore[unused-async]
        return ""

    monkeypatch.setattr(IssueFetcherAgent, "run_query", staticmethod(fake_run_query))

    with pytest.raises(RuntimeError, match="issue_fetcher did not write"):
        asyncio.run(agent(tmp_path).fetch_many("project = PROJ", tmp_path / "x.json"))
