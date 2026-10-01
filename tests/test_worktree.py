import subprocess
import tempfile
import unittest
from pathlib import Path

from meow.worktree import (
    _ensure_branch_worktree,
    _ensure_existing_branch_worktree,
    _ensure_feature_worktree,
    _reject_reserved_name,
    _require_branch_checked_out,
)


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def make_repo(tmp: str) -> Path:
    root = Path(tmp)
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    (root / "README.md").write_text("x\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


class EnsureExistingBranchWorktreeTests(unittest.TestCase):
    def test_checks_out_an_existing_local_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            git(root, "branch", "feature/x")

            worktree_dir = _ensure_existing_branch_worktree(
                root, "br-feature-x", "feature/x"
            )

            current = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=worktree_dir, capture_output=True, text=True, encoding="utf-8",
            ).stdout.strip()
            self.assertEqual(current, "feature/x")

    def test_checks_out_a_remote_only_branch(self):
        with (
            tempfile.TemporaryDirectory() as tmp1,
            tempfile.TemporaryDirectory() as tmp2,
        ):
            origin = make_repo(tmp1)
            root = Path(tmp2) / "clone"
            git(Path(tmp2), "clone", "-q", str(origin), str(root))
            git(origin, "checkout", "-q", "-b", "feature/remote-only")
            (origin / "README.md").write_text("y\n", encoding="utf-8")
            git(origin, "commit", "-q", "-am", "remote change")
            git(root, "fetch", "-q", "origin")

            worktree_dir = _ensure_existing_branch_worktree(
                root, "br-remote-only", "feature/remote-only"
            )

            current = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=worktree_dir, capture_output=True, text=True, encoding="utf-8",
            ).stdout.strip()
            self.assertEqual(current, "feature/remote-only")

    def test_reuses_an_existing_worktree_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            git(root, "branch", "feature/x")
            first = _ensure_existing_branch_worktree(root, "br-feature-x", "feature/x")

            second = _ensure_existing_branch_worktree(root, "br-feature-x", "feature/x")

            self.assertEqual(first, second)

    def test_raises_a_clear_error_when_branch_does_not_exist_anywhere(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)

            with self.assertRaisesRegex(RuntimeError, "was not found locally or as"):
                _ensure_existing_branch_worktree(root, "br-nope", "does-not-exist")


class RequireBranchCheckedOutTests(unittest.TestCase):
    def test_passes_silently_when_the_branch_matches_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            git(root, "checkout", "-q", "-b", "feature/x")

            self.assertIsNone(_require_branch_checked_out(root, "feature/x"))

    def test_raises_when_head_is_on_a_different_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            git(root, "branch", "feature/x")

            with self.assertRaisesRegex(RuntimeError, "feature/x"):
                _require_branch_checked_out(root, "feature/x")


class RejectReservedNameTests(unittest.TestCase):
    """`git worktree add .worktrees/con` fails deep inside git's own
    `.git/worktrees/con` bookkeeping with a bare "Invalid argument" -- these
    lock in that the reserved-name check catches it before git is ever
    invoked, with a message that actually explains why."""

    def test_rejects_each_reserved_name_case_insensitively(self):
        for name in ("CON", "con", "Nul", "PRN", "aux", "COM1", "lpt9", "Com5"):
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(RuntimeError, "reserved Windows"),
            ):
                _reject_reserved_name(name)

    def test_rejects_a_reserved_name_with_an_extension(self):
        with self.assertRaisesRegex(RuntimeError, "reserved Windows"):
            _reject_reserved_name("con.txt")

    def test_does_not_reject_names_that_merely_contain_one(self):
        for name in ("console", "auxiliary", "lpt10", "com0", "my-nul-feature"):
            with self.subTest(name=name):
                _reject_reserved_name(name)  # no raise

    def test_feature_worktree_rejects_before_touching_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)

            with self.assertRaisesRegex(RuntimeError, "reserved Windows"):
                _ensure_feature_worktree(root, "con")

            self.assertFalse((root / ".worktrees" / "con").exists())

    def test_branch_worktree_rejects_before_touching_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)

            with self.assertRaisesRegex(RuntimeError, "reserved Windows"):
                _ensure_branch_worktree(root, "nul", "issue/nul")

            self.assertFalse((root / ".worktrees" / "nul").exists())

    def test_existing_branch_worktree_rejects_before_touching_git(self):
        # "prn" itself can't be a real git branch (refs are files on disk
        # too, and Windows refuses those the same way) -- use a reserved
        # *feature_name* (the worktree directory) with an unrelated, real
        # branch, to isolate what this test actually checks.
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            git(root, "branch", "feature/real-branch")

            with self.assertRaisesRegex(RuntimeError, "reserved Windows"):
                _ensure_existing_branch_worktree(root, "prn", "feature/real-branch")

            self.assertFalse((root / ".worktrees" / "prn").exists())


if __name__ == "__main__":
    unittest.main()
