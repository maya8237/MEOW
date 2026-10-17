"""Project custom definitions and packaged assets cannot resolve outside the repo."""

from pathlib import Path

import pytest

from meow.project.custom.definitions import _definitions
from meow.project.custom.plugin import _build, _digest
from meow.project.custom.sources import parse_sources


def _source(root, kind, scope="project"):
    directory = root / kind
    directory.mkdir(parents=True)
    return parse_sources({kind: [kind]}, scope, root, root / ".meow" / "config.toml")[0]


def _redirect_resolution(monkeypatch, link, destination):
    # Windows developer mode is not required: simulate the resolved target of
    # a nested filesystem link while keeping ordinary directory enumeration.
    original = Path.resolve

    def resolve(path, *args, **kwargs):
        if path.is_relative_to(link):
            return destination / path.relative_to(link)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)


def _definition(source, entry):
    if entry.startswith("skill"):
        directory = source.path / "style"
        directory.mkdir()
        path = directory / "SKILL.md"
        path.write_text(
            "---\nname: style\ndescription: Style\n---\nBody\n", encoding="utf-8"
        )
        link = directory if entry == "skill-dir" else path
    elif entry == "agent":
        link = source.path / "audit.md"
        link.write_text(
            "---\nname: audit\ndescription: Audit\n---\nInspect\n", encoding="utf-8"
        )
    else:
        link = source.path / "quick.toml"
        link.write_text(
            'description="Quick"\n[[steps]]\nid="plan"\nuses="builtin.plan"\n',
            encoding="utf-8",
        )
    return link


@pytest.mark.parametrize("entry", ["skill-dir", "skill-file", "agent", "workflow"])
@pytest.mark.parametrize(
    "policy",
    [("project", True), ("project", False), ("local", True), ("user", True)],
)
def test_nested_definition_links_respect_scope(tmp_path, monkeypatch, entry, policy):
    scope, escape = policy
    root = tmp_path / "project"
    kind = {"agent": "agents", "workflow": "workflows"}.get(entry, "skills")
    source = _source(root, kind, scope)
    link = _definition(source, entry)
    destination = tmp_path / "outside" if escape else root / "shared"
    _redirect_resolution(monkeypatch, link, destination / link.name)

    if scope == "project" and escape:
        with pytest.raises(ValueError, match=r"inside.*repository"):
            _definitions(source)
    else:
        assert len(_definitions(source)) == 1


@pytest.mark.parametrize("operation", ["discover", "digest", "build"])
@pytest.mark.parametrize("entry", ["asset-file", "asset-dir"])
def test_project_skill_assets_checked_before_loading_and_packaging(
    tmp_path, monkeypatch, operation, entry
):
    source = _source(tmp_path / "project", "skills")
    directory = source.path / "style"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: style\ndescription: Style\n---\nBody\n", encoding="utf-8"
    )
    assets = directory / "assets"
    assets.mkdir()
    resource = assets / "data.txt"
    resource.write_text("private data", encoding="utf-8")
    skills = tuple(_definitions(source))
    link = resource if entry == "asset-file" else assets
    _redirect_resolution(monkeypatch, link, tmp_path / "outside" / link.name)

    with pytest.raises(ValueError, match=r"inside.*repository"):
        if operation == "discover":
            _definitions(source)
        elif operation == "digest":
            _digest(skills)
        else:
            _build(skills, tmp_path / "plugin")


def test_contained_linked_asset_changes_packaged_skill_digest(tmp_path, monkeypatch):
    source = _source(tmp_path / "project", "skills")
    directory = source.path / "style"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: style\ndescription: Style\n---\nBody\n", encoding="utf-8"
    )
    assets = directory / "assets"
    assets.mkdir()
    resource = assets / "data.txt"
    resource.write_text("first", encoding="utf-8")
    _redirect_resolution(monkeypatch, assets, source.project_dir / "shared")
    original = Path.rglob

    def rglob(path, pattern, *args, **kwargs):
        # pathlib's default traversal does not descend into directory links,
        # whereas copytree does. Model that distinction without OS link rights.
        return (
            item
            for item in original(path, pattern, *args, **kwargs)
            if assets not in item.parents
        )

    monkeypatch.setattr(Path, "rglob", rglob)
    skills = tuple(_definitions(source))
    before = _digest(skills)
    _build(skills, tmp_path / "plugin")
    assert (
        tmp_path / "plugin" / "skills" / "style" / "assets" / "data.txt"
    ).read_text() == "first"

    resource.write_text("second", encoding="utf-8")

    assert _digest(skills) != before
