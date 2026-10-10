import os

from meow.installer import _prompt


def test_path_matches_lists_only_matching_directories(tmp_path):
    (tmp_path / "alpha").mkdir()
    (tmp_path / "alpine").mkdir()
    (tmp_path / "beta").mkdir()
    (tmp_path / "alpha.txt").write_text("x", encoding="utf-8")

    matches = _prompt.path_matches(str(tmp_path / "al"))

    assert matches == [
        str(tmp_path / "alpha") + os.sep,
        str(tmp_path / "alpine") + os.sep,
    ]


def test_path_matches_is_empty_for_a_missing_parent(tmp_path):
    assert _prompt.path_matches(str(tmp_path / "missing" / "x")) == []


def test_tab_completes_then_cycles_and_typing_resets(tmp_path):
    (tmp_path / "alpha").mkdir()
    (tmp_path / "alpine").mkdir()
    line = _prompt._Line([])
    for char in str(tmp_path / "al"):
        _prompt._apply_key(line, char)

    _prompt._apply_key(line, "\t")
    first = line.text
    _prompt._apply_key(line, "\t")
    second = line.text
    _prompt._apply_key(line, "\t")

    assert first == str(tmp_path / "alpha") + os.sep
    assert second == str(tmp_path / "alpine") + os.sep
    assert line.text == first

    _prompt._apply_key(line, "\b")
    assert line.text == first[:-1]
    assert line.choices == []


def test_up_and_down_recall_earlier_entries_and_restore_the_draft():
    line = _prompt._Line(["first", "second"])
    for char in "dra":
        _prompt._apply_key(line, char)

    _prompt._apply_key(line, _prompt._UP)
    assert line.text == "second"
    _prompt._apply_key(line, _prompt._UP)
    assert line.text == "first"
    _prompt._apply_key(line, _prompt._UP)
    assert line.text == "first"
    _prompt._apply_key(line, _prompt._DOWN)
    _prompt._apply_key(line, _prompt._DOWN)
    assert line.text == "dra"


def test_remember_skips_blanks_and_immediate_repeats():
    _prompt._history.clear()
    for text in ("a", "a", "", "b"):
        _prompt._remember(text)

    assert _prompt._history == ["a", "b"]
    _prompt._history.clear()
