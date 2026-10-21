"""Quoted descriptions must survive discovery without literal escape sequences."""

import json

import pytest

from meow.project.custom.frontmatter import parse


@pytest.mark.parametrize(
    ("quoted", "expected"),
    [
        (
            json.dumps('A \U0001f431 says "hello"\nC:\\tools'),
            'A \U0001f431 says "hello"\nC:\\tools',
        ),
        ("'It''s useful'", "It's useful"),
        ('"A plain description"', "A plain description"),
    ],
)
def test_frontmatter_decodes_quoted_scalars(tmp_path, quoted, expected):
    path = tmp_path / "SKILL.md"
    path.write_text(
        f"---\nname: example\ndescription: {quoted}\n---\nBody.\n", encoding="utf-8"
    )
    metadata, body = parse(path)
    assert metadata["description"] == expected
    assert body == "Body."
