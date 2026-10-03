"""SDK usage accounting tests."""

from types import SimpleNamespace

import pytest
from claude_agent_sdk import ResultMessage

from meow.agents.base import log_stream_message
from meow.run_state import RunStore
from meow.status_cli import render
from meow.usage import usage_entry, usage_scope, usage_totals


def test_usage_entry_and_totals_keep_reported_values():
    token_count = 14
    cost = 0.25
    result = SimpleNamespace(
        num_turns=2,
        duration_ms=1200,
        total_cost_usd=cost,
        usage={"input_tokens": 10, "output_tokens": 4},
    )
    entry = usage_entry("generator", result)
    assert entry["tokens"] == token_count
    assert usage_totals({"entries": [entry]})["cost_usd"] == pytest.approx(cost)


def test_unknown_metrics_stay_unavailable():
    result = SimpleNamespace(
        num_turns=True,
        duration_ms=-1,
        total_cost_usd=float("nan"),
        usage={"input_tokens": False, "output_tokens": float("inf")},
    )
    entry = usage_entry("planner", result)
    assert entry["tokens"] is None
    assert entry["cost_usd"] is None
    assert usage_totals({"entries": [entry]})["tokens"] is None
    assert usage_totals("unavailable")["cost_usd"] is None


def test_received_results_append_without_phase_transition(tmp_path):
    result_count = 2
    total_tokens = 28
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt",
        request="change",
        repo=tmp_path,
        worktree=tmp_path,
        branch="dev",
    )
    result = ResultMessage(
        subtype="success",
        duration_ms=1200,
        duration_api_ms=800,
        is_error=False,
        num_turns=2,
        session_id="test",
        total_cost_usd=0.25,
        usage={"input_tokens": 10, "output_tokens": 4},
    )
    with usage_scope(store, record.id):
        log_stream_message("generator", result)
        log_stream_message("generator", result)
    log_stream_message("generator", result)
    saved = store.load(record.id)
    assert len(saved.usage["entries"]) == result_count
    assert usage_totals(saved.usage)["tokens"] == total_tokens
    assert saved.phase == record.phase
    assert len(saved.transitions) == len(record.transitions)
    concise = render(saved)
    assert "Tokens: 28" in concise
    assert "Turns: 4" in concise
    assert "generator" in render(saved, verbose=True)
    assert "Tokens: unavailable" in render(record)
