import pytest

from meow.project.custom.kit_store import (
    KitUnavailable,
    default_name,
    fetch,
    normalize_url,
    remote_sha,
)

from .conftest import SKILL


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("acme/kit", "https://github.com/acme/kit.git"),
        ("github:acme/kit", "https://github.com/acme/kit.git"),
        ("gitlab:platform/tools/kit", "https://gitlab.com/platform/tools/kit.git"),
        ("https://github.com/acme/kit", "https://github.com/acme/kit"),
        ("https://gitlab.com/p/t/kit.git", "https://gitlab.com/p/t/kit.git"),
        ("https://gitlab.example.com/t/kit", "https://gitlab.example.com/t/kit"),
        ("git@gitlab.example.com:t/kit.git", "git@gitlab.example.com:t/kit.git"),
    ],
)
def test_urls_with_or_without_git_suffix(text, expected):
    assert normalize_url(text) == expected


@pytest.mark.parametrize("text", ["", "-oops", "http://x/y", "ext::sh -c id", "nope"])
def test_rejects_unsafe_or_unknown_locations(text):
    with pytest.raises(ValueError):
        normalize_url(text)


def test_default_name_comes_from_repo():
    assert default_name("https://gitlab.com/a/b/My_Kit.git") == "my-kit"
    assert default_name("git@host:team/kit.git") == "kit"


def test_resolves_branch_tag_and_fetches_exact_commit(make_kit, tmp_path):
    repo = make_kit({"skills/s/SKILL.md": SKILL.format(name="s", body="one")})
    first = remote_sha(str(repo.path))
    second = repo.write({"skills/s/SKILL.md": SKILL.format(name="s", body="two")})
    assert remote_sha(str(repo.path), "main") == second != first
    assert remote_sha(str(repo.path), first) == first
    root = fetch(tmp_path / "cache", str(repo.path), first)
    assert "one" in (root / "skills/s/SKILL.md").read_text()
    assert not (root / ".git").exists()
    assert fetch(tmp_path / "cache", str(repo.path), first) == root


def test_unknown_ref_and_unreachable_remote(make_kit, tmp_path):
    repo = make_kit({"skills/s/SKILL.md": SKILL.format(name="s", body="x")})
    with pytest.raises(ValueError, match="no branch or tag"):
        remote_sha(str(repo.path), "missing")
    with pytest.raises(KitUnavailable):
        remote_sha(str(tmp_path / "absent"))


def test_kit_with_symlink_is_rejected(make_kit, tmp_path):
    repo = make_kit({"skills/s/SKILL.md": SKILL.format(name="s", body="x")})
    (repo.path / "skills/s/link").symlink_to("/etc/hostname")
    sha = repo.write({})
    with pytest.raises(ValueError, match="symbolic link"):
        fetch(tmp_path / "cache", str(repo.path), sha)
    assert not list((tmp_path / "cache").glob("*/*"))
