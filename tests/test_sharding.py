"""Tests for the CI sharding helpers in conftest."""

from types import SimpleNamespace

import pytest

from tests.conftest import parse_shard, select_shard, slow_first


def _items(**counts):
    return [
        SimpleNamespace(nodeid=f"tests/{mod}.py::test_{n}")
        for mod, count in counts.items()
        for n in range(count)
    ]


def test_shards_partition_the_suite_and_keep_order():
    items = _items(a=5, b=3, c=3, d=1)
    seen = []
    for shard in (1, 2, 3):
        selected, deselected = select_shard(items, shard, 3)
        assert len(selected) + len(deselected) == len(items)
        assert [i for i in items if i in selected] == selected
        seen += selected
    assert sorted(i.nodeid for i in seen) == sorted(i.nodeid for i in items)


def test_slow_modules_spread_across_shards():
    items = _items(slow=6, fast=6)
    for shard in (1, 2, 3):
        selected, _ = select_shard(items, shard, 3)
        assert {i.nodeid.split("::")[0] for i in selected} == {
            "tests/slow.py",
            "tests/fast.py",
        }


def test_single_shard_selects_everything():
    items = _items(a=2, b=2)
    assert select_shard(items, 1, 1) == (items, [])


@pytest.mark.parametrize("spec", ["", "2", "0/3", "4/3", "a/b", "1/2/3"])
def test_bad_spec_is_a_usage_error(spec):
    with pytest.raises(pytest.UsageError):
        parse_shard(spec)


def test_slow_modules_run_first_without_reordering_the_rest():
    items = [
        SimpleNamespace(nodeid=f"tests/{mod}::t{n}")
        for mod, n in [
            ("a.py", 0),
            ("installer/test_windows_script.py", 0),
            ("b.py", 0),
            ("installer/test_uninstall.py", 0),
            ("installer/test_windows_script.py", 1),
        ]
    ]
    assert [i.nodeid for i in slow_first(items)] == [
        "tests/installer/test_windows_script.py::t0",
        "tests/installer/test_uninstall.py::t0",
        "tests/installer/test_windows_script.py::t1",
        "tests/a.py::t0",
        "tests/b.py::t0",
    ]
