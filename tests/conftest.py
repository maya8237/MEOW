"""Shared pytest fixtures."""

import pytest

from meow.project import repos


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path_factory, monkeypatch):
    """Keep the developer's real ~/.meow config and repo registry out of tests."""
    home = tmp_path_factory.mktemp("home")
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(home))
    monkeypatch.setattr(repos, "registry_path", lambda: home / "repos.toml")
