import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from meow.agents.tester import VerificationAgent, architecture_context
from meow.infrastructure.test_runner import (
    BrowserEvidence,
    VerificationCommandEvidence,
    VerificationStageEvidence,
)
from meow.project import prompts


class Context:
    def __init__(self, root: Path, config=None):
        self.repo_dir = root
        self.use_worktree = False
        self.config = config or {
            "models": {"tester": "haiku"},
            "docs_dir": "docs/exec-plans/active",
            "lint_timeout": 60,
            "tester": {
                "tests": [],
                "dev_server": [],
                "mcp": [],
                "test_dirs": [],
                "base_url": None,
                "architecture_files": [],
            },
        }

    def model(self, role, agent=None):
        return self.config["models"].get(role)

    @staticmethod
    def lint_commands():
        return []

    def active_working_dir(self):
        return self.repo_dir


class TesterAgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.context = Context(self.root)
        (self.root / "docs").mkdir()

    async def _run_tester(self, verdict, evidence=None, config=None):
        plan = self.root / "feature.md"
        plan.write_text("Plan body", encoding="utf-8")
        reports = iter(verdict) if isinstance(verdict, tuple) else None

        def write_report(prompt, options, role):
            report = plan.with_name("feature-test.md")
            report.write_text(next(reports) if reports else verdict, encoding="utf-8")

        with patch.object(
            VerificationAgent, "run_query", new=AsyncMock(side_effect=write_report)
        ) as run:
            status, text = await VerificationAgent(
                Context(self.root, config) if config else self.context
            ).test_plan(plan, evidence or VerificationStageEvidence())
        return status, text, run, plan

    async def test_reviewer_and_tester_verdicts_use_separate_files(self):
        status, _, run, _ = await self._run_tester("SUMMARY: observed\nSTATUS: PASS")
        self.assertEqual(status, "PASS")
        self.assertTrue((self.root / "feature-test.md").exists())
        self.assertFalse((self.root / "feature-review.md").exists())
        self.assertIn("feature-test.md", run.await_args.args[1].system_prompt)

    async def test_mandatory_test_failure_overrides_tester_pass(self):
        result = VerificationCommandEvidence(
            self.root, "pytest", 1, "failed", False, True
        )
        status, text, _, _ = await self._run_tester(
            "SUMMARY: all good\nSTATUS: PASS", VerificationStageEvidence((result,))
        )
        self.assertEqual(status, "FAIL")
        self.assertIn("Mandatory test command failed", text)
        self.assertNotIn("STATUS: PASS", text)
        saved = (self.root / "feature-test.md").read_text(encoding="utf-8")
        self.assertEqual(saved, text)

    async def test_browser_artifacts_and_flows_reach_tester_prompt(self):
        browser = BrowserEvidence(
            "project-browser",
            "command",
            "passed",
            required=True,
            flows=("home page",),
            artifacts=(".meow/home.png",),
            exit_code=0,
        )
        _, _, run, _ = await self._run_tester(
            "SUMMARY: checked\nSTATUS: PASS",
            VerificationStageEvidence(browser=(browser,)),
        )
        prompt = run.await_args.args[0]
        self.assertIn("home page", prompt)
        self.assertIn(".meow/home.png", prompt)

    async def test_empty_test_list_still_launches_exploratory_tester(self):
        status, _, run, _ = await self._run_tester("SUMMARY: explored\nSTATUS: PASS")
        self.assertEqual(status, "PASS")
        run.assert_awaited_once()

    async def test_malformed_verdict_fails_clearly(self):
        for report in (
            "SUMMARY: incomplete",
            "## SUMMARY\nAll tests pass.\n## STATUS: PASS",
            "SUMMARY: all tests pass\nSTATUS: **PASS**",
        ):
            with self.subTest(report=report):
                status, text, run, _ = await self._run_tester(report)
                self.assertEqual(status, "FAIL")
                self.assertIn("Tester verdict is missing a plain STATUS: line", text)
                self.assertIn("STATUS: FAIL", text)
                self.assertEqual(
                    (self.root / "feature-test.md").read_text(encoding="utf-8"), text
                )
                self.assertEqual(run.await_count, 2)

        valid = "SUMMARY: all tests passed\nSTATUS: PASS"
        for malformed in ("## STATUS: PASS", "STATUS: **PASS**"):
            with self.subTest(malformed=malformed):
                status, text, run, _ = await self._run_tester((malformed, valid))
                self.assertEqual((status, text), ("PASS", valid))
                self.assertEqual(run.await_count, 2)
                self.assertIn("Preserve the findings", run.await_args.args[0])

    async def test_stale_report_is_not_accepted_without_a_fresh_verdict(self):
        plan = self.root / "feature.md"
        plan.write_text("Plan body", encoding="utf-8")
        report = self.root / "feature-test.md"
        report.write_text("SUMMARY: stale pass\nSTATUS: PASS\n", encoding="utf-8")
        with patch.object(VerificationAgent, "run_query", new=AsyncMock()):
            status, text = await VerificationAgent(self.context).test_plan(
                plan, VerificationStageEvidence()
            )
        self.assertEqual(status, "FAIL")
        self.assertIn("did not produce", text)
        self.assertIn("stale pass", report.read_text(encoding="utf-8"))

    async def test_mcp_config_passes_through_and_absent_mcp_adds_none(self):
        config = dict(self.context.config)
        config["tester"] = dict(config["tester"])
        config["tester"]["mcp"] = [
            {
                "name": "browser",
                "command": "npx",
                "args": ["browser-mcp"],
                "env": {"MODE": "test"},
            }
        ]
        _, _, run, _ = await self._run_tester(
            "STATUS: PASS\nSUMMARY: ok", config=config
        )
        self.assertEqual(
            run.await_args.args[1].mcp_servers["browser"]["args"], ["browser-mcp"]
        )
        self.assertIn("mcp__browser__*", run.await_args.args[1].allowed_tools)
        _, _, run, _ = await self._run_tester("STATUS: PASS\nSUMMARY: ok")
        self.assertFalse(run.await_args.args[1].mcp_servers)

    def test_architecture_reads_docs_and_adds_explicit_files(self):
        docs = self.root / "docs" / "ARCHITECTURE.md"
        extra = self.root / "apps" / "ARCHITECTURE.md"
        extra.parent.mkdir()
        docs.write_text("docs architecture", encoding="utf-8")
        extra.write_text("component architecture", encoding="utf-8")
        content = architecture_context(self.root, [Path("apps/ARCHITECTURE.md")])
        self.assertIn("docs architecture", content)
        self.assertIn("component architecture", content)
        docs.unlink()
        self.assertIn("No architecture document", architecture_context(self.root, []))

    def test_missing_architecture_is_reported_as_optional_context(self):
        self.assertIn("No architecture document", architecture_context(self.root, []))


if __name__ == "__main__":
    unittest.main()


def test_write_tests_is_off_by_default_and_opt_in():
    report = Path("report.md")
    default = prompts.tester_prompt(report)
    assert "Do not create scratch files" in default
    assert "Add new test files" not in default
    enabled = prompts.tester_prompt(report, write_tests=True)
    assert "Add new test files" in enabled
    assert "Do not create scratch files" not in enabled


def test_changed_files_lists_uncommitted_and_untracked(tmp_path):
    import subprocess

    from meow.agents.tester import changed_files

    def git(*argv):
        subprocess.run(["git", "-C", str(tmp_path), *argv], check=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "init")
    assert "none uncommitted" in changed_files(tmp_path)
    (tmp_path / "a.py").write_text("y", encoding="utf-8")
    (tmp_path / "new.py").write_text("z", encoding="utf-8")
    listing = changed_files(tmp_path)
    assert "a.py" in listing and "new.py" in listing


def test_changed_files_is_capped(tmp_path):
    import subprocess

    from meow.agents.tester import changed_files

    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    for n in range(5):
        (tmp_path / f"f{n}.py").write_text("x", encoding="utf-8")
    assert "and 3 more" in changed_files(tmp_path, limit=2)


def test_short_plan_prompt_is_opt_in():
    from meow.project.custom.definitions import discover
    from meow.project.custom.rendering import render_agent
    from meow.project.custom.sources import builtin_sources

    planner = discover(builtin_sources()).agent("planner")
    base = {"plan_file": Path("p.md")}
    assert "Keep this plan short" not in render_agent(planner, base)
    assert "Keep this plan short" in render_agent(planner, {**base, "short_plan": True})
