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


def test_nested_mapping_is_kept_as_raw_text(tmp_path):
    from meow.project.custom import frontmatter

    path = tmp_path / "SKILL.md"
    path.write_text(
        "---\nname: x\nhooks:\n  PostToolUse:\n    - matcher: Edit\n"
        "tools:\n  - A\n---\nbody\n",
        encoding="utf-8",
    )

    data, body = frontmatter.parse(path)

    assert data["hooks"].startswith("PostToolUse:") and data["tools"] == ["A"]
    assert body == "body"
