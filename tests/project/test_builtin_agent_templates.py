"""Built-in agent templates use only documented variables."""

import pytest

from meow.project.custom import builtin_dir
from meow.project.custom.definitions import load_agent
from meow.project.custom.rendering import unknown_variables
from meow.project.custom.sources import CustomSource


@pytest.mark.parametrize(
    "path", sorted((builtin_dir() / "agents").glob("*.toml")), ids=lambda p: p.stem
)
def test_builtin_agents_use_only_documented_variables(path):
    source = CustomSource("agents", path.parent, "builtin", None)

    assert unknown_variables(load_agent(path, source)) == []
