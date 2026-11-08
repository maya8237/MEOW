"""Renaming a folder aside instead of deleting it."""

from meow.common.atomic import set_aside


def test_set_aside_keeps_contents_and_picks_free_names(tmp_path):
    assert set_aside(tmp_path / "missing") is None
    for expected in ("old.bak", "old.bak-2"):
        folder = tmp_path / "old"
        folder.mkdir()
        (folder / "kept.txt").write_text("x")
        backup = set_aside(folder)
        assert backup == tmp_path / expected
        assert (backup / "kept.txt").read_text() == "x"
        assert not folder.exists()
