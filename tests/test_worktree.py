import subprocess
import tempfile
import unittest
from pathlib import Path

from meow.worktree import _ensure_existing_branch_worktree, _require_branch_checked_out


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


if __name__ == "__main__":
    unittest.main()
