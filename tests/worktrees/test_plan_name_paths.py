from pathlib import Path

import pytest

from meow.project.plan_files import planned_plan_file


@pytest.mark.parametrize("name", ["../evil", "a/b", "a\\b", ".."])
def test_feature_name_must_be_a_plain_file_name(name: str):
    with pytest.raises(ValueError):
        planned_plan_file(Path("docs"), name)


def test_empty_feature_name_uses_default_plan():
    assert planned_plan_file(Path("docs"), "") == Path("docs/plan.md")
