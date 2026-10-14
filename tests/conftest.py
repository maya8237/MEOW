"""Shared pytest fixtures."""

import pytest

from meow.project import repos


@pytest.fixture(autouse=True)
def _offline_update_checks(monkeypatch):
    """Never contact the release server from unit tests."""
    from meow.installer import maintenance

    def offline(*args, **kwargs):
        raise OSError("Release server disabled in tests")

    monkeypatch.setattr(maintenance, "urlopen", offline)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path_factory, monkeypatch):
    """Keep the developer's real ~/.meow config and repo registry out of tests."""
    home = tmp_path_factory.mktemp("home")
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(home))
    monkeypatch.setattr(repos, "registry_path", lambda: home / "repos.toml")
