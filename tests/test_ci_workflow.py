"""Regression coverage for .github/workflows/ci.yml's Windows shell pin.

windows-latest runs `run:` steps under pwsh by default, and pwsh 7.4+'s
$PSNativeCommandUseErrorActionPreference (on by default) turns any stderr
output from a native process into a terminating step failure under GitHub
Actions' $ErrorActionPreference = Stop -- regardless of the process's actual
exit code. `python -m unittest discover -v` always writes its "test ... ok"
progress to stderr by design, so every matrix run on windows-latest failed
deterministically until the test-suite step was pinned to bash. No YAML
library is a project dependency, so this is a plain text assertion rather
than a parsed-structure one.
"""

import unittest
from pathlib import Path

WORKFLOW = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"
)


class TestSuiteStepShellTests(unittest.TestCase):
    def test_run_the_test_suite_step_pins_bash(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        step = text.split("- name: Run the test suite", 1)[1]
        step = step.split("\n      - name:", 1)[0]

        self.assertIn("shell: bash", step)
        self.assertIn("unittest discover", step)
