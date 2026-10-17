"""Project source paths remain portable after filesystem links are resolved."""

from pathlib import Path

import pytest

from meow.project.custom.sources import _resolve, conventional_sources


def test_project_source_rejects_resolved_escape(tmp_path, monkeypatch):
    project = tmp_path / "project"
    outside = tmp_path / "private"
    project.mkdir()
    outside.mkdir()
    original = Path.resolve

    def resolve(path, *args, **kwargs):
        if path == project / "linked":
            return outside
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(ValueError, match="stay inside"):
        _resolve("linked", "project", project, "test config")


def test_conventional_project_source_rejects_resolved_escape(tmp_path, monkeypatch):
    project = tmp_path / "project"
    outside = tmp_path / "private"
    linked = project / ".meow" / "agents"
    linked.mkdir(parents=True)
    outside.mkdir()
    original = Path.resolve

    def resolve(path, *args, **kwargs):
        return outside if path == linked else original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(ValueError, match="stay inside"):
        conventional_sources(project, tmp_path / "user")


@pytest.mark.parametrize("scope", ["project", "local", "user"])
def test_source_directory_symlink_respects_scope(tmp_path, scope):
    project = tmp_path / "project"
    outside = tmp_path / "private"
    project.mkdir()
    outside.mkdir()
    try:
        (project / "linked").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Directory symlinks unavailable: {exc}")

    if scope == "project":
        with pytest.raises(ValueError, match="stay inside"):
            _resolve("linked", scope, project, "test config")
    else:
        assert _resolve("linked", scope, project, "test config") == outside.resolve()
