"""Git/worktree bootstrapping for the harness's working directory.

Kept separate from `orchestrator.py`'s agent wiring and round-loop
orchestration: this module only ever touches the filesystem and `git`, and
has no dependency on `Sprint`, config, or any agent.
"""

import shutil
import subprocess
from pathlib import Path


def _ensure_gitignore_entry(working_dir: Path, entry: str = ".worktrees/") -> None:
    """Ensure the repo's .gitignore includes the given ignored path."""
    gitignore = working_dir / ".gitignore"
    contents = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    lines = contents.splitlines()
    normalized = {line.strip() for line in lines}
    if entry in normalized or entry.rstrip("/") in normalized:
        return

    with gitignore.open("a", encoding="utf-8") as handle:
        if contents and not contents.endswith("\n"):
            handle.write("\n")
        handle.write(f"{entry}\n")


class DirtyWorkingTreeError(RuntimeError):
    """The git working tree has uncommitted changes meow refuses to build on."""


def _is_linked_worktree(path: Path) -> bool:
    """True if `path` is a linked git worktree rather than the main checkout.

    A linked worktree's own `--git-dir` (its private per-worktree state
    under `<main>/.git/worktrees/<name>`) differs from `--git-common-dir`
    (the repository shared with the main checkout and every other worktree);
    the main checkout's are the same path. Used to tell resumable,
    feature-scoped WIP from the main repo's own uncommitted changes, which
    `_ensure_clean_tree` still guards. False if git is unavailable or `path`
    isn't inside a git repo at all.
    """
    git = shutil.which("git")
    if not git:
        return False
    git_dir = _run_git([git, "rev-parse", "--git-dir"], cwd=path)
    common_dir = _run_git([git, "rev-parse", "--git-common-dir"], cwd=path)
    if git_dir.returncode != 0 or common_dir.returncode != 0:
        return False
    return (
        (path / git_dir.stdout.strip()).resolve()
        != (path / common_dir.stdout.strip()).resolve()
    )


def _is_gitignore_change(status_line: str) -> bool:
    """True if one `git status --porcelain` line is only about `.gitignore`."""
    return status_line[3:].strip() == ".gitignore"


def _ensure_clean_tree(working_dir: Path) -> None:
    """Raise `DirtyWorkingTreeError` if the repo has uncommitted changes.

    Must run before `_boot_repo`, which may itself edit `.gitignore`. Skipped
    when `working_dir` isn't a git repo, git is unavailable, or `working_dir`
    is itself a linked worktree -- resuming one with uncommitted changes from
    a prior partial run is expected, not accidental, so only the main repo's
    own cleanliness is enforced here.

    A dirty `.gitignore` alone never blocks: `_boot_repo` is the only thing
    meow itself ever edits in the main repo, it never commits that edit, and
    it runs on every invocation -- without this, a fresh project's very next
    invocation would be blocked by meow's own bookkeeping rather than
    anything a user needs to act on. `.gitignore` changes alongside other
    real changes still show up in the reported list; only the block itself
    is skipped when `.gitignore` is the sole thing dirty.
    """
    git = shutil.which("git")
    if not git:
        return
    if _is_linked_worktree(working_dir):
        return
    result = _run_git([git, "status", "--porcelain"], cwd=working_dir)
    if result.returncode != 0 or not result.stdout.strip():
        return
    changes = result.stdout.rstrip().splitlines()
    blocking_changes = [line for line in changes if not _is_gitignore_change(line)]
    if not blocking_changes:
        return
    raise DirtyWorkingTreeError(
        f"{working_dir} has {len(changes)} uncommitted change(s); commit or "
        "stash them before running meow:\n" + "\n".join(changes)
    )


def _boot_repo(working_dir: Path, *, include_gitignore: bool = True) -> None:
    """Run working-directory boot checks every meow command needs."""
    if include_gitignore:
        _ensure_gitignore_entry(working_dir)


def _resolve_working_dir(
    working_dir: Path,
    *,
    use_worktree: bool,
    feature_name: str | None,
    source_branch: str | None = None,
) -> tuple[Path, str | None, bool]:
    """Return the active directory and optional feature name.

    If `working_dir` is already a linked git worktree, it is used in place,
    the same as if `use_worktree` were False -- pointing `--working-dir` at
    an existing worktree means "work here", not "nest another worktree
    inside it". `source_branch`, when given, is the branch a freshly
    created worktree checks out instead of the main checkout's current
    HEAD; see `_ensure_feature_worktree`.
    """
    if not use_worktree or _is_linked_worktree(working_dir):
        return working_dir, feature_name, False
    if not feature_name:
        raise ValueError("feature_name is required when use_worktree=True")

    return (
        _ensure_feature_worktree(
            working_dir, feature_name, source_branch=source_branch
        ),
        feature_name,
        True,
    )


def _registered_worktree_paths(git: str, working_dir: Path) -> set[Path]:
    """Absolute paths git currently has registered as worktrees of this repo."""
    result = _run_git([git, "worktree", "list", "--porcelain"], cwd=working_dir)
    if result.returncode != 0:
        return set()
    return {
        Path(line.removeprefix("worktree ").strip()).resolve()
        for line in result.stdout.splitlines()
        if line.startswith("worktree ")
    }


def _ensure_feature_worktree(
    working_dir: Path, feature_name: str, *, source_branch: str | None = None
) -> Path:
    """Create a per-feature worktree under the repo's .worktrees directory.

    Raises if `git worktree add` fails, rather than silently falling back to
    a plain, non-git directory -- a generator session pointed at such a
    directory would run against an empty project with no error ever
    surfaced. Also raises if an existing `.worktrees/<feature_name>`
    directory is no longer a registered worktree (e.g. left behind after
    `git worktree remove` elsewhere, or created without git) -- reusing it
    silently would only fail confusingly later, deep in some agent's own
    git calls.

    `source_branch`, when given, checks the new worktree out from that
    branch instead of the main checkout's current HEAD -- ignored when an
    existing worktree is being reused rather than created, since resuming
    one takes priority over re-branching it.

    Callers must not pass a `working_dir` that is itself already a linked
    worktree -- `_resolve_working_dir` routes that case around this
    function entirely, using it in place instead of nesting another one
    inside it.
    """
    worktrees_dir = working_dir / ".worktrees"
    worktree_dir = worktrees_dir / feature_name
    git = shutil.which("git")

    if worktree_dir.exists():
        if git and worktree_dir.resolve() not in _registered_worktree_paths(
            git, working_dir
        ):
            raise RuntimeError(
                f"{worktree_dir} exists but is not a registered git "
                f"worktree of {working_dir} -- it may have been removed "
                "with `git worktree remove` while the directory itself was "
                "left behind, or created without git. Delete it and rerun."
            )
        return worktree_dir

    worktrees_dir.mkdir(parents=True, exist_ok=True)

    if not git:
        raise RuntimeError(
            "git is not installed or not on PATH; cannot create a feature "
            f"worktree at {worktree_dir}."
        )

    argv = [
        git,
        "-C",
        str(working_dir),
        "worktree",
        "add",
        "--detach",
        str(worktree_dir),
    ]
    if source_branch:
        argv.append(source_branch)

    try:
        subprocess.run(argv, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Failed to create worktree at {worktree_dir}: "
            f"{exc.stderr.strip() or exc}"
        ) from exc

    return worktree_dir


def _run_git(argv: list[str], *, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)


def _ensure_branch_worktree(
    working_dir: Path, feature_name: str, branch_name: str
) -> Path:
    """Create (or reuse) a worktree checked out on a real, pushable branch.

    Unlike `_ensure_feature_worktree` (detached, never pushed -- what `run`/
    `plan` need), `meow issue` always needs a real branch to hand back to
    its caller, so it creates its own worktree on one instead.
    """
    worktree_dir = working_dir / ".worktrees" / feature_name
    if worktree_dir.exists():
        return worktree_dir

    git = shutil.which("git")
    if not git:
        raise RuntimeError(
            "git is required for `meow issue`'s worktree/branch/push steps."
        )

    (working_dir / ".worktrees").mkdir(parents=True, exist_ok=True)
    branch_exists = _run_git(
        [git, "rev-parse", "--verify", "--quiet", branch_name], cwd=working_dir
    ).returncode == 0

    if branch_exists:
        argv = [git, "worktree", "add", str(worktree_dir), branch_name]
    else:
        argv = [git, "worktree", "add", "-b", branch_name, str(worktree_dir)]
    result = _run_git(argv, cwd=working_dir)
    if result.returncode != 0:
        raise RuntimeError(
            f"Could not create worktree for branch '{branch_name}':\n{result.stderr}"
        )
    return worktree_dir


def _push_branch(worktree_dir: Path, branch_name: str) -> None:
    """Push a finished `meow issue` branch to `origin`."""
    git = shutil.which("git")
    remotes = _run_git([git, "remote"], cwd=worktree_dir).stdout.split()
    if "origin" not in remotes:
        raise RuntimeError(
            "No 'origin' remote configured -- `meow issue` can't push the "
            f"finished branch '{branch_name}'. Add one with `git remote add "
            "origin <url>`, or push it yourself."
        )

    result = _run_git([git, "push", "-u", "origin", branch_name], cwd=worktree_dir)
    if result.returncode != 0:
        raise RuntimeError(
            f"git push failed for branch '{branch_name}':\n{result.stderr}"
        )
