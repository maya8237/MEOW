"""Shared pytest fixtures."""

import pytest

from meow.project import repos


@pytest.fixture(autouse=True)
def _isolated_repo_registry(tmp_path, monkeypatch):
    """Keep `remember()` from writing the developer's real repo registry."""
    monkeypatch.setattr(repos, "registry_path", lambda: tmp_path / "repos.toml")
