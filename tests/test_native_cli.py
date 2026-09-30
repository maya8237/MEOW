import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_native import git, make_repo

from meow import cli


def run_native(*argv: str) -> tuple[int, str, str]:
    """Run `meow native ...` in-process, returning (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with (
        patch("sys.argv", ["meow", "native", *argv]),
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(err),
    ):
        try:
            cli.cli_main()
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


class NativeCliTests(unittest.TestCase):
    def test_prepare_prints_one_json_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)

            code, out, _ = run_native("prepare", "--name", "feat", "-d", str(root))

            self.assertEqual(code, 0)
            data = json.loads(out)
            self.assertTrue(Path(data["active_dir"]).is_dir())
            self.assertEqual(data["max_rounds"], 2)

    def test_dirty_tree_exits_nonzero_with_message_on_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            (root / "README.md").write_text("dirty\n")

            code, out, err = run_native("prepare", "--name", "feat", "-d", str(root))

            self.assertEqual(code, 1)
            self.assertEqual(out, "")
            self.assertIn("uncommitted", err.lower())

    def test_allow_dirty_skips_the_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            (root / "README.md").write_text("dirty\n")

            code, _, _ = run_native(
                "prepare", "--name", "feat", "--allow-dirty", "-d", str(root)
            )

            self.assertEqual(code, 0)

    def test_round_advances_and_reports_exhaustion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            args = ("round", "docs/plans/feat.md", "-d", str(root))

            rounds = [json.loads(run_native(*args)[1]) for _ in range(3)]

            self.assertEqual([r["round"] for r in rounds], [1, 2, 2])
            self.assertEqual([r["exhausted"] for r in rounds], [False, False, True])
            reset = json.loads(run_native(*args, "--reset")[1])
            self.assertEqual(reset["round"], 0)

    def test_verdict_and_lookups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            review = root / "docs" / "plans" / "feat-review.md"
            review.write_text("SUMMARY: ok\nSTATUS: PASS\n")

            verdict = json.loads(run_native("verdict", str(review), "-d", str(root))[1])
            plan = json.loads(run_native("latest-plan", "-d", str(root))[1])
            latest = json.loads(run_native("latest-review", "-d", str(root))[1])

            self.assertEqual(verdict["status"], "PASS")
            self.assertTrue(plan["plan_file"].endswith("feat.md"))
            self.assertEqual(latest["flavor"], "plan")

    def test_prompt_command_and_missing_plan_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)

            code, out, _ = run_native(
                "prompt", "reviewer-plan", "--plan", "docs/plans/feat.md",
                "-d", str(root),
            )
            bad_code, _, err = run_native("prompt", "reviewer-plan", "-d", str(root))

            self.assertEqual(code, 0)
            self.assertIn("Sprint Contract", json.loads(out)["system_prompt"])
            self.assertEqual(bad_code, 1)
            self.assertIn("--plan", err)

    def test_lint_command_reports_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)

            code, out, _ = run_native("lint", "-d", str(root))

            self.assertEqual(code, 0)
            self.assertTrue(json.loads(out)["clean"])

    def test_unknown_role_is_rejected_by_argparse(self):
        code, _, _ = run_native("prompt", "wizard")

        self.assertNotEqual(code, 0)

    def test_push_command_pushes_to_a_real_remote(self):
        with tempfile.TemporaryDirectory() as tmp:
            bare = Path(tmp) / "origin.git"
            git(Path(tmp), "init", "-q", "--bare", str(bare))
            repo_dir = Path(tmp) / "repo"
            repo_dir.mkdir()
            root = make_repo(str(repo_dir))
            git(root, "remote", "add", "origin", str(bare))
            branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=root, capture_output=True, text=True,
            ).stdout.strip()

            code, out, _ = run_native("push", branch, "-d", str(root))

            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out), {"branch": branch, "pushed": True})
            refs = subprocess.run(
                ["git", "branch", "--list", branch],
                cwd=bare, capture_output=True, text=True,
            ).stdout
            self.assertIn(branch, refs)

    def test_push_command_without_origin_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)

            code, out, err = run_native("push", "does-not-matter", "-d", str(root))

            self.assertEqual(code, 1)
            self.assertEqual(out, "")
            self.assertIn("origin", err)


class BranchReviewNativeCliTests(unittest.TestCase):
    def test_prepare_existing_branch_creates_worktree_on_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            git(root, "branch", "feature/x")

            code, out, _ = run_native(
                "prepare", "--existing-branch", "feature/x",
                "--name", "br-feature-x", "--allow-dirty", "-d", str(root),
            )

            self.assertEqual(code, 0)
            data = json.loads(out)
            self.assertTrue(data["use_worktree"])
            self.assertTrue(Path(data["active_dir"]).is_dir())

    def test_prepare_existing_branch_that_does_not_exist_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)

            code, out, err = run_native(
                "prepare", "--existing-branch", "nope",
                "--name", "br-nope", "--allow-dirty", "-d", str(root),
            )

            self.assertEqual(code, 1)
            self.assertEqual(out, "")
            self.assertIn("nope", err)

    def test_prompt_reviewer_branch_needs_target_and_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            git(root, "branch", "main")
            git(root, "branch", "feature/x")

            code, out, _ = run_native(
                "prompt", "reviewer-branch",
                "--target", "main", "--branch", "feature/x", "-d", str(root),
            )
            bad_code, _, err = run_native("prompt", "reviewer-branch", "-d", str(root))

            self.assertEqual(code, 0)
            data = json.loads(out)
            self.assertIn("'main'", data["system_prompt"])
            self.assertIn("'feature/x'", data["system_prompt"])
            self.assertEqual(bad_code, 1)
            self.assertIn("--target", err)
