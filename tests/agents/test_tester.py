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

    def model(self, role):
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

        def write_report(prompt, options, role):
            report = plan.with_name("feature-test.md")
            report.write_text(verdict, encoding="utf-8")

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
        status, _, _, _ = await self._run_tester("SUMMARY: incomplete")
        self.assertEqual(status, "FAIL")

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
