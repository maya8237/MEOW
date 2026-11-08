"""Fetch kits from any git remote into a shared, immutable cache.

A kit is a git repository (GitHub, GitLab, self-hosted, or a local path) holding
optional `skills/`, `agents/`, `workflows/`, and `hooks/` folders. Each commit
is stored once under `~/.meow/kits/<repo>/<sha>/`, without `.git`, and never
modified, so a lock file pinning a sha always reproduces the same files.
"""

import hashlib
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

from meow.common.atomic import move_directory

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_SHORTHAND = re.compile(r"^(github|gitlab):([\w.-]+(?:/[\w.-]+)+?)(?:\.git)?$")
_BARE = re.compile(r"^[\w.-]+/[\w.-]+?(?:\.git)?$")
_SCP = re.compile(r"^[\w.-]+@[\w.-]+:[\w./~-]+$")
_HOSTS = {"github": "github.com", "gitlab": "gitlab.com"}
_SCHEMES = ("https://", "ssh://", "git://", "file://")
_PROTOCOLS = "file:git:https:ssh"


def _make_writable_and_retry(function, path, error) -> None:
    """`shutil.rmtree` hook: Windows refuses to delete read-only files (git's
    object files), so clear the flag once and retry."""
    if not isinstance(error, PermissionError):
        raise error
    os.chmod(path, stat.S_IWRITE)
    function(path)


def remove_tree(path: Path, *, quiet: bool = False) -> None:
    """Delete a folder tree, including read-only files; `quiet` ignores failures."""
    try:
        shutil.rmtree(path, onexc=_make_writable_and_retry)
    except OSError:
        if not quiet:
            raise


class KitUnavailable(OSError):
    """The remote could not be reached; the current copy stays in use."""


def normalize_url(text: str) -> str:
    """Turn `github:o/r`, `gitlab:g/s/p`, `o/r`, or a full URL into a git URL."""
    text = text.strip()
    if not text or text.startswith("-"):
        raise ValueError(f"not a kit location: {text!r}")
    if match := _SHORTHAND.match(text):
        return f"https://{_HOSTS[match[1]]}/{match[2]}.git"
    if text.startswith(_SCHEMES) or _SCP.match(text) or Path(text).is_absolute():
        return text
    if _BARE.match(text):
        return f"https://github.com/{text.removesuffix('.git')}.git"
    raise ValueError(
        f"not a kit location: {text!r}; use owner/repo, github:owner/repo, "
        "gitlab:group/project, a git URL, or an absolute path"
    )


def default_name(url: str) -> str:
    cleaned = url.replace("\\", "/").rstrip("/").removesuffix(".git")
    leaf = cleaned.replace(":", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[^a-z0-9]+", "-", leaf.lower()).strip("-")
    return name or "kit"


def _git(args: list[str], *, cwd: Path | None = None, timeout: float = 30):
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "never",  # Git Credential Manager would open a window
        "GIT_ALLOW_PROTOCOL": _PROTOCOLS,
    }
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise KitUnavailable(f"git failed: {exc}") from exc
    if result.returncode:
        raise KitUnavailable(result.stderr.strip() or f"git {args[0]} failed")
    return result.stdout


def remote_sha(url: str, ref: str | None = None, timeout: float = 20) -> str:
    """The commit `ref` (branch, tag, sha; default branch when None) points to."""
    ref = ref or "HEAD"
    if _HEX40.match(ref):
        return ref
    if ref.startswith("-"):
        raise ValueError(f"invalid ref {ref!r}")
    patterns = (
        ["HEAD"]
        if ref == "HEAD"
        else [f"refs/heads/{ref}", f"refs/tags/{ref}", f"refs/tags/{ref}^{{}}"]
    )
    found = {}
    for line in _git(["ls-remote", url, *patterns], timeout=timeout).splitlines():
        sha, _, name = line.partition("\t")
        found[name] = sha
    for name in (f"refs/tags/{ref}^{{}}", f"refs/heads/{ref}", f"refs/tags/{ref}", ref):
        if name in found:
            return found[name]
    raise ValueError(f"{url}: no branch or tag named {ref!r}")


def kit_dir(home: Path, url: str, sha: str) -> Path:
    leaf = re.sub(r"[^\w.-]+", "-", default_name(url))
    return home / f"{leaf}-{hashlib.sha1(url.encode()).hexdigest()[:10]}" / sha


def _download(url: str, sha: str, repo: Path, timeout: float) -> None:
    """Shallow-fetch one commit; hosts that refuse that get a full clone."""
    try:
        repo.mkdir()
        _git(["init", "--quiet"], cwd=repo)
        _git(["fetch", "--quiet", "--depth", "1", url, sha], cwd=repo, timeout=timeout)
        _git(["checkout", "--quiet", "--detach", "FETCH_HEAD"], cwd=repo)
    except KitUnavailable:
        remove_tree(repo, quiet=True)
        _git(["clone", "--quiet", "--no-checkout", url, str(repo)], timeout=timeout)
        _git(["checkout", "--quiet", "--detach", sha], cwd=repo, timeout=120)


def fetch(home: Path, url: str, sha: str, timeout: float = 300) -> Path:
    """Store `sha` of `url` in the cache (a no-op when already there)."""
    target = kit_dir(home, url, sha)
    if target.is_dir():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".fetch-", dir=target.parent))
    try:
        repo = work / "repo"
        _download(url, sha, repo, timeout)
        remove_tree(repo / ".git")
        for path in repo.rglob("*"):
            if path.is_symlink() or path.is_junction():
                raise ValueError(f"{url}: kit contains a symbolic link: {path.name}")
        try:
            move_directory(repo, target)
        except OSError:
            if not target.is_dir():  # another process may have stored it first
                raise
    finally:
        remove_tree(work, quiet=True)
    return target


def tree(directory: Path) -> dict[str, bytes]:
    """File contents by relative path, to compare two cached folders."""
    if not directory.is_dir():
        return {}
    return {
        p.relative_to(directory).as_posix(): p.read_bytes()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }
