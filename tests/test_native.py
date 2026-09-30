import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from meow import native
from meow.worktree import DirtyWorkingTreeError

OK_CMD = "python ok.py"
FAIL_CMD = "python fail.py"

TOML = f"""max_rounds = 2
docs_dir = "docs/plans"

[[lint]]
command = {json.dumps(OK_CMD)}
per_file = false

[[lint]]
command = {json.dumps(FAIL_CMD)}
per_file = false
gate = false
"""


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def make_repo(tmp: str) -> Path:
    root = Path(tmp)
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    (root / ".harness.toml").write_text(TOML, encoding="utf-8")
    (root / "docs" / "plans").mkdir(parents=True)
    (root / "docs" / "plans" / "feat.md").write_text("# plan\n", encoding="utf-8")
    (root / "README.md").write_text("x\n", encoding="utf-8")
    (root / "ok.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    (root / "fail.py").write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


def write_config(root: Path, command: str) -> None:
    (root / ".harness.toml").write_text(
        f'docs_dir = "docs/plans"\n[[lint]]\ncommand = {json.dumps(command)}\n',
        encoding="utf-8",
    )


class PrepareTests(unittest.TestCase):
    def test_creates_feature_worktree_and_reports_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)

            result = native.prepare(root, native.PrepareOptions(name="feat"))

            self.assertTrue(result["use_worktree"])
            self.assertTrue(Path(result["active_dir"]).is_dir())
            self.assertTrue(result["plan_file"].endswith("feat.md"))
            self.assertTrue(result["review_file"].endswith("feat-review.md"))
            self.assertEqual(result["max_rounds"], 2)
            self.assertEqual(len(result["lint"]), 2)
            self.assertIn(".worktrees/", (root / ".gitignore").read_text())

    def test_dirty_tree_is_refused_like_the_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            (root / "README.md").write_text("changed\n", encoding="utf-8")

            with self.assertRaises(DirtyWorkingTreeError):
                native.prepare(root, native.PrepareOptions(name="feat"))

    def test_no_worktree_uses_project_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)

            result = native.prepare(
                root, native.PrepareOptions(use_worktree=False, require_clean=False)
            )

            self.assertFalse(result["use_worktree"])
            self.assertEqual(Path(result["active_dir"]), root)
            self.assertTrue(result["plan_file"].endswith("plan.md"))

    def test_branch_worktree_requires_a_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)

            with self.assertRaises(ValueError):
                native.prepare(root, native.PrepareOptions(branch="issue/X-1"))

    def test_branch_worktree_is_created_on_the_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)

            result = native.prepare(
                root, native.PrepareOptions(name="issue-x-1", branch="issue/X-1")
            )

            head = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=result["active_dir"], capture_output=True, text=True,
            ).stdout.strip()
            self.assertEqual(head, "issue/X-1")


class LookupTests(unittest.TestCase):
    def test_latest_plan_ignores_reviews_and_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            docs = root / "docs" / "plans"
            (docs / "feat-review.md").write_text("STATUS: FAIL\n")
            (docs / "feat.native-state.json").write_text("{}")

            result = native.latest_plan(root, root)

            self.assertTrue(result["plan_file"].endswith("feat.md"))

    def test_latest_review_reports_flavor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            (root / "docs" / "plans" / "feat-review.md").write_text("STATUS: PASS\n")

            result = native.latest_review(root, root)

            self.assertEqual(result["flavor"], "plan")

    def test_latest_plan_without_plans_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            (root / "docs" / "plans" / "feat.md").unlink()

            with self.assertRaises(FileNotFoundError):
                native.latest_plan(root, root)


class VerdictTests(unittest.TestCase):
    def test_reads_status_and_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            review = Path(tmp) / "r.md"
            review.write_text("SUMMARY: looks fine\nSTATUS: PASS\n- ok\n")

            self.assertEqual(
                native.verdict(review), {"status": "PASS", "summary": "looks fine"}
            )

    def test_missing_status_defaults_to_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            review = Path(tmp) / "r.md"
            review.write_text("no verdict here\n")

            self.assertEqual(
                native.verdict(review), {"status": "FAIL", "summary": None}
            )


class LintTests(unittest.TestCase):
    def test_project_lint_splits_blocking_from_informational(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)

            result = native.lint(root, root, native.LintOptions())

            self.assertTrue(result["clean"])
            self.assertEqual(result["blocking"], [])
            self.assertEqual(len(result["informational"]), 1)

    def test_missing_program_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            write_config(root, "no-such-linter-xyz")

            result = native.lint(root, root, native.LintOptions(file_path="a.py"))

            self.assertFalse(result["clean"])
            self.assertIn("Could not run lint", result["problems"][0])

    def test_per_file_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            write_config(root, "python fail.py")

            result = native.lint(root, root, native.LintOptions(file_path="a.py"))

            self.assertFalse(result["clean"])

    def test_all_blocking_ignores_gate(self):
        """lint-fix's native mode must fix every command CLI mode would,
        not just the ones a review would gate on."""
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)

            gated = native.lint(root, root, native.LintOptions())
            ungated = native.lint(root, root, native.LintOptions(all_blocking=True))

            # Default (gated): the gate=false command's failure is
            # informational only, so `clean` is true despite it.
            self.assertEqual(gated["blocking"], [])
            self.assertEqual(len(gated["informational"]), 1)
            self.assertTrue(gated["clean"])
            # `all_blocking=True`: the same failure now counts as blocking.
            self.assertEqual(ungated["informational"], [])
            self.assertEqual(ungated["blocking"], gated["informational"])
            self.assertFalse(ungated["clean"])


class RoundTests(unittest.TestCase):
    def test_counts_up_then_reports_exhausted_without_advancing(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = Path(tmp) / "feat.md"

            first = native.round_state(plan, 2, "next")
            second = native.round_state(plan, 2, "next")
            third = native.round_state(plan, 2, "next")

            self.assertEqual((first["round"], first["exhausted"]), (1, False))
            self.assertEqual((second["round"], second["exhausted"]), (2, False))
            self.assertEqual((third["round"], third["exhausted"]), (2, True))
            self.assertEqual(native.round_state(plan, 2, "show")["round"], 2)

    def test_reset_starts_over(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = Path(tmp) / "feat.md"
            native.round_state(plan, 3, "next")

            self.assertEqual(native.round_state(plan, 3, "reset")["round"], 0)
            self.assertEqual(native.round_state(plan, 3, "next")["round"], 1)

    def test_corrupt_state_file_starts_at_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = Path(tmp) / "feat.md"
            (Path(tmp) / "feat.native-state.json").write_text("not json")

            self.assertEqual(native.round_state(plan, 3, "next")["round"], 1)

    def test_state_file_is_json_beside_the_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = Path(tmp) / "feat.md"
            native.round_state(plan, 3, "next")

            state = Path(tmp) / "feat.native-state.json"
            self.assertEqual(json.loads(state.read_text()), {"round": 1})

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            native.round_state(Path("x.md"), 1, "bogus")


class PromptTests(unittest.TestCase):
    def test_plan_reviewer_prompt_matches_the_sdk_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            plan = root / "docs" / "plans" / "feat.md"

            result = native.role_prompt(
                root, root, "reviewer-plan", plan_file=plan, focus="speed"
            )

            self.assertIn("Sprint Contract in", result["system_prompt"])
            self.assertIn(
                "Pay particular attention to: speed.", result["system_prompt"]
            )
            self.assertEqual(result["query"], f"Review {plan}")
            self.assertTrue(result["review_file"].endswith("feat-review.md"))

    def test_rules_are_appended(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            (root / "docs" / "RULES.md").write_text("global\n## reviewer\nbe strict\n")
            plan = root / "docs" / "plans" / "feat.md"

            result = native.role_prompt(root, root, "reviewer-plan", plan_file=plan)

            self.assertIn("be strict", result["system_prompt"])

    def test_generator_needs_a_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)

            with self.assertRaises(ValueError):
                native.role_prompt(root, root, "generator")

    def test_unknown_role_is_rejected(self):
        with self.assertRaises(ValueError):
            native.role_prompt(Path("."), Path("."), "nope")

    def test_prompt_reviewer_uses_diff_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            (root / "README.md").write_text("changed\n")

            result = native.role_prompt(root, root, "reviewer-prompt")

            self.assertIn("git diff contains changes", result["system_prompt"])
            self.assertTrue(result["review_file"].endswith("review.md"))


class RolePromptDispatchTests(unittest.TestCase):
    """Every `role_prompt` branch, not just the two exercised above --
    a wiring bug in the `builders` dict is otherwise only caught by
    `prompts.py`'s own unit tests, which never go through `role_prompt`."""

    def test_planner_prompt_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            plan = root / "docs" / "plans" / "feat.md"

            result = native.role_prompt(root, root, "planner", plan_file=plan)

            self.assertIn(str(plan), result["system_prompt"])
            self.assertIsNone(result["query"])

    def test_generator_prompt_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            plan = root / "docs" / "plans" / "feat.md"

            result = native.role_prompt(root, root, "generator", plan_file=plan)

            self.assertIn(str(plan), result["system_prompt"])
            self.assertIn("Sprint Contract", result["system_prompt"])

    def test_explorer_prompt_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)

            result = native.role_prompt(root, root, "explorer")

            self.assertIn(str(root), result["system_prompt"])
            self.assertEqual(result["model"], "haiku")

    def test_review_fixer_prompt_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)

            result = native.role_prompt(root, root, "review-fixer")

            self.assertIn("fix the review findings", result["system_prompt"])

    def test_lint_fixer_prompt_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)

            result = native.role_prompt(root, root, "lint-fixer")

            self.assertIn("fix lint failures", result["system_prompt"])

    def test_reviewer_mr_prompt_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)

            result = native.role_prompt(root, root, "reviewer-mr")

            self.assertIn("merge request", result["system_prompt"])
            self.assertIsNone(result["query"])
            self.assertTrue(result["review_file"].endswith("gitlab-review.md"))


class PushTests(unittest.TestCase):
    def test_push_without_origin_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)

            with self.assertRaises(RuntimeError):
                native.push(root, "does-not-matter")

    def test_push_succeeds_against_a_real_remote(self):
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

            result = native.push(root, branch)

            self.assertEqual(result, {"branch": branch, "pushed": True})
            refs = subprocess.run(
                ["git", "branch", "--list", branch],
                cwd=bare, capture_output=True, text=True,
            ).stdout
            self.assertIn(branch, refs)
