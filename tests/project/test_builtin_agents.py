"""Model precedence between `[models]` config and agent definitions."""

from dataclasses import replace
from pathlib import Path

from meow.agents.base import ProjectContext
from meow.project.custom.rendering import builtin_agent


def _context(models: dict) -> ProjectContext:
    return ProjectContext(Path("."), {"models": models})


def test_configured_model_beats_the_definition():
    planner = replace(builtin_agent("planner"), model="from-definition")

    assert _context({"planner": "from-config"}).model("planner", planner) == (
        "from-config"
    )


def test_definition_model_applies_when_config_leaves_it_unset():
    planner = replace(builtin_agent("planner"), model="from-definition")

    assert _context({"planner": None}).model("planner", planner) == "from-definition"
    assert _context({}).model("planner", planner) == "from-definition"


def test_without_either_the_reviewer_fallback_is_unchanged():
    planner = builtin_agent("planner")
    context = _context({"reviewer": "rev", "planner": None})

    assert context.model("planner", planner) is None
    assert context.model("docs_updater", None) == "rev"


def test_builtin_explorer_ships_its_model():
    assert builtin_agent("explorer").model == "haiku"


def test_explorer_resolves_to_haiku_by_default():
    from meow.agents.explorer import ExplorerAgent
    from meow.project.config import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["models"]["explorer"] is None
    context = ProjectContext(Path("."), {"models": dict(DEFAULT_CONFIG["models"])})

    assert ExplorerAgent(context).definition().model == "haiku"
    context.config["models"]["explorer"] = "opus"
    assert ExplorerAgent(context).definition().model == "opus"
