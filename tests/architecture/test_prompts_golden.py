"""Role prompts rendered from builtin agent files match what MEOW said before
the move (see `prompt_cases`), up to whitespace."""

import json
from pathlib import Path

import pytest

from meow.project import prompts
from meow.project.custom import builtin_dir
from meow.project.custom.definitions import load_agent
from meow.project.custom.rendering import unknown_variables
from meow.project.custom.sources import CustomSource
from tests.architecture.prompt_cases import cases

GOLDEN = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "prompts_golden.json").read_text(
        encoding="utf-8"
    )
)


def _words(text: str) -> str:
    return " ".join(text.split())


@pytest.mark.parametrize(("case_id", "builder", "args", "kwargs"), cases())
def test_rendered_prompt_matches_recorded_text(case_id, builder, args, kwargs):
    rendered = getattr(prompts, builder)(*args, **kwargs)

    assert _words(rendered) == _words(GOLDEN[case_id])


def test_every_case_is_recorded():
    assert {case[0] for case in cases()} == set(GOLDEN)


@pytest.mark.parametrize(
    "path", sorted((builtin_dir() / "agents").glob("*.toml")), ids=lambda p: p.stem
)
def test_builtin_agents_use_only_documented_variables(path):
    source = CustomSource("agents", path.parent, "builtin", None)

    assert unknown_variables(load_agent(path, source)) == []
