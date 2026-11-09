"""Kits installed from a plain local folder, for kit authors."""

import pytest

from meow.project.custom import kit_ops
from meow.project.custom.kit_store import local_location


def _kit(tmp_path):
    root = tmp_path / "my-kit"
    (root / "skills/hello").mkdir(parents=True)
    (root / "skills/hello/SKILL.md").write_text(
        "---\nname: hello\ndescription: hi\n---\nSay hi.\n", encoding="utf-8"
    )
    return root


def test_relative_and_plain_absolute_folders_are_local(tmp_path, monkeypatch):
    root = _kit(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert local_location("./my-kit") == f"local:{root}"
    assert local_location(str(root)) == f"local:{root}"
    assert local_location("owner/repo") is None


def test_absolute_git_repository_stays_a_git_kit(tmp_path):
    root = _kit(tmp_path)
    (root / ".git").mkdir()
    assert local_location(str(root)) is None


def test_install_links_in_place_and_survives_update(tmp_path):
    root = _kit(tmp_path)
    project, user = tmp_path / "project", tmp_path / "user"
    project.mkdir()
    user.mkdir()
    spec = kit_ops.install(project, user, str(root), output=lambda _: None)
    assert spec.url == f"local:{root}"
    assert "linked folder" in kit_ops.listing(project, user)[0]
    assert any("always current" in x for x in kit_ops.update(project, user))
    assert "hello" in "\n".join(kit_ops.show(project, user, "my-kit"))
    kit_ops.uninstall(project, user, "my-kit")
    assert root.is_dir()  # the author's folder is never deleted


def test_local_kit_cannot_be_a_project_kit(tmp_path):
    root = _kit(tmp_path)
    with pytest.raises(ValueError, match="only on this machine"):
        kit_ops.install(tmp_path, tmp_path / "u", str(root), scope="project")
