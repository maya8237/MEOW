import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_native import make_repo

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
