import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from meow.agents.tester import VerificationAgent
from meow.infrastructure.test_runner import VerificationStageEvidence
from meow.project.plan_files import stage_file
from meow.project.stray_verdicts import recover_stray, stray_copies
from tests.agents.test_tester import TEST_PLAN, Context

PASS = "SUMMARY: ok\nSTATUS: PASS" + TEST_PLAN


class StrayVerdictTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.context = Context(self.root)
        self.expected = stage_file(
            self.root / self.context.config["docs_dir"], "tester"
        )
        self.stray = self.root / "plans" / "tester.stage.md"
        self.stray.parent.mkdir()

    async def _run(self, side_effect):
        with patch.object(
            VerificationAgent, "run_query", new=AsyncMock(side_effect=side_effect)
        ):
            return await VerificationAgent(self.context).test_request(
                "Audit page", VerificationStageEvidence()
            )

    def test_helpers_skip_expected_path_and_ignored_dirs(self):
        self.expected.parent.mkdir(parents=True)
        self.expected.write_text("x", encoding="utf-8")
        hidden = self.root / "node_modules" / "tester.stage.md"
        hidden.parent.mkdir()
        hidden.write_text("x", encoding="utf-8")
        self.stray.write_text("x", encoding="utf-8")
        self.assertEqual(stray_copies(self.root, self.expected), [self.stray])

    async def test_untracked_stray_is_deleted_before_the_run(self):
        self.stray.write_text("stale", encoding="utf-8")
        seen = []

        def write_report(prompt, options, role):
            seen.append(self.stray.exists())
            self.expected.write_text(PASS, encoding="utf-8")

        status, _ = await self._run(write_report)
        self.assertEqual((status, seen), ("PASS", [False]))

    async def test_tracked_stray_is_left_alone(self):
        self.stray.write_text("tracked", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(self.root), "add", "plans/tester.stage.md"], check=True
        )

        def write_report(prompt, options, role):
            self.expected.write_text(PASS, encoding="utf-8")

        await self._run(write_report)
        self.assertEqual(self.stray.read_text(encoding="utf-8"), "tracked")

    async def test_verdict_written_to_stray_path_is_recovered(self):
        def write_report(prompt, options, role):
            self.stray.write_text(PASS, encoding="utf-8")

        status, text = await self._run(write_report)
        self.assertEqual((status, text), ("PASS", PASS))
        self.assertEqual(self.expected.read_text(encoding="utf-8"), PASS)
        self.assertFalse(self.stray.exists())

    async def test_stale_tracked_stray_is_not_recovered(self):
        self.stray.write_text(PASS, encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(self.root), "add", "plans/tester.stage.md"], check=True
        )
        old = time.time() - 3600
        os.utime(self.stray, (old, old))

        status, text = await self._run(lambda prompt, options, role: None)
        self.assertEqual(status, "FAIL")
        self.assertIn("did not produce a readable verdict", text)
        self.assertIsNone(recover_stray(self.root, self.expected, time.time() - 60))


if __name__ == "__main__":
    unittest.main()
