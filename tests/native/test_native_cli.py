import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_native import TOML, git, make_repo

from meow import cli, native


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


class NativeVerifyTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows executable quoting")
    def test_verify_recognizes_quoted_windows_launcher(self):
        from meow.config import TestCommand

        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            command = TestCommand(Path("."), f'"{sys.executable}" -c "pass"')
            with patch("meow.native.shutil.which", return_value=sys.executable):
                result = native._verify_command(command, root)
        self.assertTrue(result["launcher_available"])
        self.assertEqual(result["status"], "ready_unchecked")

    def test_verify_reports_unconfigured_tester_without_running_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            result = native.verify(root, run_lint=False)

            self.assertFalse(result["tester"]["configured"])
            self.assertFalse(result["tester"]["connection_tested"])
            self.assertFalse(result["tester"]["architecture_available"])

    def test_verify_reports_component_commands_and_optional_architecture(  # ruff: ignore[too-many-statements]
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            (root / "apps" / "web").mkdir(parents=True)
            (root / "services" / "api").mkdir(parents=True)
            (root / "docs" / "ARCHITECTURE.md").write_text("architecture\n")
            tester_toml = """
[tester]
architecture_files = ["docs/OPTIONAL.md"]
test_dirs = ["apps/web/tests", "services/api/tests"]

[[tester.tests]]
cwd = "apps/web"
command = "python -m unittest"
env = {PRIVATE_TOKEN = "actual-secret-do-not-print", MISSING = "$MEOW_MISSING"}

[[tester.tests]]
cwd = "services/api"
command = "python -m pytest"

[[tester.dev_server]]
cwd = "apps/web"
command = "python server.py"
ready_url = "http://localhost:3000"

[[tester.dev_server]]
cwd = "services/api"
command = "python server.py"
ready_url = "http://localhost:8000"

[[tester.mcp]]
name = "browser"
command = "missing-tester-mcp"
env = {TOKEN = "actual-secret-do-not-print"}
"""
            (root / ".harness.toml").write_text(TOML + tester_toml, encoding="utf-8")

            def find_command(name):
                return None if name == "missing-tester-mcp" else "/python"

            with patch("meow.native.shutil.which", side_effect=find_command):
                result = native.verify(root, run_lint=False)

            tester = result["tester"]
            self.assertEqual(len(tester["tests"]), 2)
            self.assertEqual(len(tester["dev_servers"]), 2)
            self.assertEqual(tester["mcp"][0]["status"], "command_unavailable")
            self.assertEqual(
                tester["tests"][0]["unresolved_environment"],
                ["MEOW_MISSING"],
            )
            self.assertEqual(tester["tests"][0]["status"], "missing_environment")
            self.assertNotIn("actual-secret-do-not-print", json.dumps(tester))
            self.assertTrue(tester["architecture_available"])
            self.assertEqual(len(tester["test_directories"]), 2)
            optional_doc = next(
                item
                for item in tester["architecture"]
                if item["path"].endswith("OPTIONAL.md")
            )
            self.assertFalse(optional_doc["exists"])

    def test_verify_reports_missing_command_cwd_as_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            with (root / ".harness.toml").open("a", encoding="utf-8") as config_file:
                config_file.write(
                    '\n[[tester.tests]]\ncwd = "missing"\n'
                    'command = "python -m unittest"\n'
                )

            result = native.verify(root, run_lint=False)

            self.assertEqual(result["tester"]["tests"][0]["status"], "invalid_cwd")

    def test_verify_native_cli_returns_tester_readiness_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)
            code, out, _ = run_native("verify", "--no-lint", "-d", str(root))

            self.assertEqual(code, 0)
            self.assertIn("tester", json.loads(out))

    def test_reports_optional_integrations_as_unconfigured(self):
        result = native._verify_jira({})
        self.assertEqual(result["status"], "not_configured")
        self.assertFalse(result["mcp"]["connection_tested"])
        self.assertEqual(native._verify_gitlab({})["status"], "not_configured")

    def test_checks_mcp_command_and_environment_without_exposing_values(self):
        with patch("meow.native.shutil.which", return_value="/usr/bin/uvx"):
            result = native._verify_jira({
                "jira": {
                    "project_key": "MEOW",
                    "mcp": {"command": "uvx", "args": ["mcp-atlassian"]},
                }
            })

        self.assertEqual(result["status"], "missing_environment")
        self.assertTrue(result["project_key_configured"])
        self.assertEqual(result["mcp"]["required_environment_missing"], ["JIRA_URL"])
        self.assertNotIn("environment_values", result["mcp"])
        self.assertFalse(result["mcp"]["connection_tested"])


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
                "prompt",
                "reviewer-plan",
                "--plan",
                "docs/plans/feat.md",
                "-d",
                str(root),
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
                cwd=root,
                capture_output=True,
                text=True,
            ).stdout.strip()

            code, out, _ = run_native("push", branch, "-d", str(root))

            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out), {"branch": branch, "pushed": True})
            refs = subprocess.run(
                ["git", "branch", "--list", branch],
                cwd=bare,
                capture_output=True,
                text=True,
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
                "prepare",
                "--existing-branch",
                "feature/x",
                "--name",
                "br-feature-x",
                "--allow-dirty",
                "-d",
                str(root),
            )

            self.assertEqual(code, 0)
            data = json.loads(out)
            self.assertTrue(data["use_worktree"])
            self.assertTrue(Path(data["active_dir"]).is_dir())

    def test_prepare_existing_branch_that_does_not_exist_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(tmp)

            code, out, err = run_native(
                "prepare",
                "--existing-branch",
                "nope",
                "--name",
                "br-nope",
                "--allow-dirty",
                "-d",
                str(root),
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
                "prompt",
                "reviewer-branch",
                "--target",
                "main",
                "--branch",
                "feature/x",
                "-d",
                str(root),
            )
            bad_code, _, err = run_native("prompt", "reviewer-branch", "-d", str(root))

            self.assertEqual(code, 0)
            data = json.loads(out)
            self.assertIn("'main'", data["system_prompt"])
            self.assertIn("'feature/x'", data["system_prompt"])
            self.assertEqual(bad_code, 1)
            self.assertIn("--target", err)
