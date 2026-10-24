"""Shared pytest fixtures."""

import logging
import os

import pytest
import structlog

from meow.project import repos


@pytest.fixture(autouse=True)
def _restore_logging():
    """`configure_logging()` binds a handler to whatever stderr is current; a
    test that leaves it behind would later write to a closed capture stream."""
    handlers, level = logging.root.handlers[:], logging.root.level
    yield
    logging.root.handlers, logging.root.level = handlers, level
    structlog.reset_defaults()


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


def parse_shard(spec):
    """Return (shard, total) from an `N/TOTAL` spec, or raise `pytest.UsageError`."""
    try:
        shard, total = (int(part) for part in spec.split("/"))
    except ValueError:
        shard = total = 0
    if not 1 <= shard <= total:
        raise pytest.UsageError(f"MEOW_TEST_SHARD must look like N/TOTAL, got {spec!r}")
    return shard, total


def select_shard(items, shard, total):
    """Split `items` into (selected, deselected) for 1-based `shard` of `total`.

    Tests are dealt round-robin in collection order, so slow modules spread
    across shards and each keeps definition order. Every xdist worker collects
    the same list, so they all compute the same split."""
    selected, deselected = [], []
    for index, item in enumerate(items):
        (selected if index % total == shard - 1 else deselected).append(item)
    return selected, deselected


def pytest_collection_modifyitems(config, items):
    """Run one slice of the suite when `MEOW_TEST_SHARD` is `N/TOTAL`."""
    spec = os.environ.get("MEOW_TEST_SHARD")
    if not spec:
        return
    selected, deselected = select_shard(items, *parse_shard(spec))
    config.hook.pytest_deselected(items=deselected)
    items[:] = selected
