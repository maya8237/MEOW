import json
from pathlib import Path

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
        ("acme/kit", "https://gitlab.com/acme/kit.git"),
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


def _register(path, servers, project=None):
    data = {"mcpServers": servers}
    if project:
        data = {"projects": {str(project): {"mcpServers": servers}}}
    path.write_text(json.dumps(data), encoding="utf-8")


def _server(name, **env):
    return {"command": name, "args": [], "env": env}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_shorthands_use_user_scope_mcp_hosts(workspace):
    _register(
        Path.home() / ".claude.json",
        {
            "gitlab": _server(
                "gitlab-mcp", GITLAB_API_URL="https://git.acme.io/api/v4"
            ),
            "github": _server("github-mcp-server", GITHUB_HOST="gh.acme.io"),
        },
    )
    assert normalize_url("a/kit") == "https://git.acme.io/a/kit.git"
    assert normalize_url("gitlab:g/s/kit") == "https://git.acme.io/g/s/kit.git"
    assert normalize_url("github:a/kit") == "https://gh.acme.io/a/kit.git"
    assert normalize_url("https://x.io/a/kit") == "https://x.io/a/kit"


def test_shorthands_use_project_mcp_json_and_user_project_scope(workspace):
    _register(
        workspace / ".mcp.json",
        {"gitlab": _server("npx", GITLAB_URL="https://user:pw@gl.acme.io:8443/sub/")},
    )
    _register(
        Path.home() / ".claude.json",
        {"github": _server("github-mcp-server", GITHUB_HOST="https://ghe.acme.io")},
        project=workspace.resolve(),
    )
    assert normalize_url("a/kit") == "https://gl.acme.io:8443/sub/a/kit.git"
    assert normalize_url("github:a/kit") == "https://ghe.acme.io/a/kit.git"


def test_shorthands_fall_back_to_public_hosts(workspace, monkeypatch):
    monkeypatch.delenv("UNSET_GL", raising=False)
    _register(
        Path.home() / ".claude.json",
        {
            "gitlab-a": _server("gitlab", GITLAB_API_URL="https://a.io/api/v4"),
            "gitlab-b": _server("gitlab", GITLAB_API_URL="https://b.io/api/v4"),
            "github": _server("github", GITHUB_HOST="${UNSET_GL}"),
        },
    )
    assert normalize_url("a/kit") == "https://gitlab.com/a/kit.git"
    assert normalize_url("github:a/kit") == "https://github.com/a/kit.git"
