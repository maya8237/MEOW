import argparse
import ast
import re
import unittest
from pathlib import Path

from meow.cli import cli
from meow.native import native
from meow.native.native_cli import add_native_parser

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
SHARED = SKILLS_DIR / "_shared" / "native-mode.md"
PROJECT_ROOT = SKILLS_DIR.parent

# skill directory -> the CLI command its "CLI mode" section must keep
CLI_FALLBACKS = {
    "run": "meow run",
    "plan": "meow plan",
    "review": "meow review",
    "lint": "meow run --lint-fix --report-only",
}


def native_commands() -> set[str]:
    parser = argparse.ArgumentParser(prog="meow")
    add_native_parser(parser.add_subparsers(dest="command"))
    top = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    inner = next(
        action
        for action in top.choices["native"]._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    return set(inner.choices)


def all_markdown() -> dict[Path, str]:
    return {path: path.read_text(encoding="utf-8") for path in SKILLS_DIR.rglob("*.md")}


def configured_cli_skills() -> set[str]:
    skills = set()
    for path in (PROJECT_ROOT / "src" / "meow" / "agents").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "skills":
                skills.update(ast.literal_eval(node.value))
    return skills


def frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, "missing frontmatter"
    return dict(
        line.split(": ", 1) for line in match.group(1).splitlines() if ": " in line
    )


class SkillStructureTests(unittest.TestCase):  # ruff: ignore[too-many-public-methods]
    def test_every_expected_skill_exists_with_matching_frontmatter(self):
        for name in CLI_FALLBACKS:
            with self.subTest(skill=name):
                text = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
                meta = frontmatter(text.replace("\r\n", "\n"))

                self.assertEqual(meta["name"], name)
                self.assertTrue(meta["description"].strip())

    def test_no_unexpected_skill_directories(self):
        actual = {path.parent.name for path in SKILLS_DIR.glob("*/SKILL.md")}

        self.assertEqual(actual - {"onboard", "migration"}, set(CLI_FALLBACKS))

    def test_each_skill_keeps_native_and_cli_sections(self):
        for name, command in CLI_FALLBACKS.items():
            with self.subTest(skill=name):
                text = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")

                self.assertIn("## Native mode", text)
                self.assertIn("## CLI mode", text)
                cli_section = text.split("## CLI mode", 1)[1]
                self.assertIn(command, cli_section)
                self.assertIn("../_shared/native-mode.md", text)

    def test_shared_protocol_exists(self):
        self.assertTrue(SHARED.is_file())

    def test_shared_protocol_covers_cli_skills_and_every_native_prompt_role(self):
        text = SHARED.read_text(encoding="utf-8")
        cli_skills = configured_cli_skills()

        for skill in cli_skills:
            with self.subTest(skill=skill):
                self.assertIn(skill, text)

        for role in native.PROMPT_ROLES:
            with self.subTest(role=role):
                self.assertIn(f"`{role}`", text)

        for native_role in (
            "Review fixer",
            "Lint fixer",
            "Jira issue fetcher",
            "GitLab merge-request fetcher",
        ):
            with self.subTest(native_role=native_role):
                self.assertIn(native_role, text)

    def test_lint_skill_uses_the_shared_lint_fixer_prompt(self):
        text = (SKILLS_DIR / "lint" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("meow native prompt lint-fixer", text)

    def test_test_mode_routes_run_and_explicit_plan_review_through_cli(self):
        run = (SKILLS_DIR / "run" / "SKILL.md").read_text(encoding="utf-8")
        review = (SKILLS_DIR / "review" / "SKILL.md").read_text(encoding="utf-8")
        shared = SHARED.read_text(encoding="utf-8")

        self.assertIn("--test", run)
        self.assertIn("CLI mode", run)
        self.assertIn("meow review --plan-file PATH --test", review)
        self.assertIn("Without `--test`, keep the native flow", review)
        self.assertIn("configured servers stay alive", shared)

    def test_run_skill_defaults_to_cli_for_automatic_preplan(self):
        run = (
            Path(__file__).resolve().parents[2] / "skills" / "run" / "SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn("CLI mode is the default", run)
        self.assertIn("Native mode is an explicit opt-in", run)

    def test_onboarding_treats_tester_as_optional(self):
        onboard = (SKILLS_DIR / "onboard" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("Tester mode", onboard)
        self.assertIn("yes/no", onboard)
        self.assertNotIn("yes/no/later", onboard)

    def test_onboarding_offers_scheduled_jira_without_windows_lock_in(self):
        onboard = (SKILLS_DIR / "onboard" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("Unattended scheduled Jira runs", onboard)
        self.assertIn("Windows Task Scheduler", onboard)
        self.assertIn("cron", onboard)
        self.assertIn("systemd", onboard)
        self.assertNotIn("required schedule setup", onboard)

    def test_every_cited_native_command_exists(self):
        known = native_commands()
        cited = set()
        for text in all_markdown().values():
            cited.update(re.findall(r"meow native ([a-z][a-z-]*)", text))

        self.assertTrue(cited)
        self.assertLessEqual(cited, known)

    def test_every_helper_command_is_documented_in_the_shared_protocol(self):
        text = SHARED.read_text(encoding="utf-8")

        for command in native_commands():
            with self.subTest(command=command):
                self.assertIn(f"`{command}", text)

    def test_every_cited_prompt_role_exists(self):
        cited = set()
        for text in all_markdown().values():
            cited.update(re.findall(r"meow native prompt ([a-z][a-z-]*)", text))

        self.assertLessEqual(cited, set(native.PROMPT_ROLES))

    def test_native_group_is_registered_on_the_real_cli(self):
        parser = cli._build_arg_parser()

        args = parser.parse_args(["native", "verdict", "x.md"])

        self.assertEqual(args.native_command, "verdict")
