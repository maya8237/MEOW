"""Tests for the CI sharding helpers in conftest."""

from types import SimpleNamespace

import pytest

from tests.conftest import parse_shard, select_shard


def _items(**counts):
    return [
        SimpleNamespace(nodeid=f"tests/{mod}.py::test_{n}")
        for mod, count in counts.items()
        for n in range(count)
    ]


def test_shards_partition_the_suite_without_splitting_modules():
    items = _items(a=5, b=3, c=3, d=1)
    seen = []
    for shard in (1, 2, 3):
        selected, deselected = select_shard(items, shard, 3)
        assert len(selected) + len(deselected) == len(items)
        assert [i for i in items if i in selected] == selected
        seen += selected
    assert sorted(i.nodeid for i in seen) == sorted(i.nodeid for i in items)
    for shard in (1, 2, 3):
        selected, _ = select_shard(items, shard, 3)
        modules = {i.nodeid.split("::")[0] for i in selected}
        for other in (1, 2, 3):
            if other != shard:
                theirs = {
                    i.nodeid.split("::")[0] for i in select_shard(items, other, 3)[0]
                }
                assert not modules & theirs


def test_single_shard_selects_everything():
    items = _items(a=2, b=2)
    assert select_shard(items, 1, 1) == (items, [])


@pytest.mark.parametrize("spec", ["", "2", "0/3", "4/3", "a/b", "1/2/3"])
def test_bad_spec_is_a_usage_error(spec):
    with pytest.raises(pytest.UsageError):
        parse_shard(spec)
