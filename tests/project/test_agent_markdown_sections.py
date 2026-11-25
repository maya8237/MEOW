"""Markdown agents hold prompt variants and fragments as trailing sections."""

import pytest

from meow.project.custom.agent_markdown import to_markdown
from meow.project.custom.definitions import load_agent
from meow.project.custom.sources import CustomSource

AGENT = """---
name: checker
description: Checks things.
---
Check {{request}}. {{> rules}}

<!-- meow:fragment rules -->
Be strict.

<!-- meow:variant quick -->
Glance at {{request}}. {{> rules}}
"""


def _source(tmp_path):
    return CustomSource("agents", tmp_path, "project", None)


def _load(tmp_path, text):
    path = tmp_path / "checker.md"
    path.write_text(text, encoding="utf-8")
    return load_agent(path, _source(tmp_path))


def test_sections_split_off_the_prompt(tmp_path):
    agent = _load(tmp_path, AGENT)
    assert agent.prompt == "Check {{request}}. {{> rules}}"
    assert agent.fragments == {"rules": "Be strict."}
    assert agent.variants == {"quick": "Glance at {{request}}. {{> rules}}"}


@pytest.mark.parametrize(
    ("section", "message"),
    [
        ("<!-- meow:other x -->\ntext", "unknown section"),
        ("<!-- meow:fragment rules -->\n", "is empty"),
        ("<!-- meow:fragment rules -->\nb\n<!-- meow:fragment rules -->\nc", "twice"),
    ],
)
def test_bad_sections_name_the_file(tmp_path, section, message):
    text = "---\nname: checker\ndescription: d\n---\nBody\n" + section
    with pytest.raises(ValueError, match=message) as error:
        _load(tmp_path, text)
    assert "checker.md" in str(error.value)


def test_to_markdown_round_trips_variants_and_fragments(tmp_path):
    agent = _load(tmp_path, AGENT)
    again = _load(tmp_path, to_markdown(agent))
    assert (again.prompt, again.variants, again.fragments) == (
        agent.prompt,
        agent.variants,
        agent.fragments,
    )
