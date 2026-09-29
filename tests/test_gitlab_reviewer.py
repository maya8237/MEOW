import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from meow import gitlab_reviewer


class LoadGitlabConfigTests(unittest.TestCase):
    def test_raises_when_gitlab_table_is_missing(self):
        with self.assertRaisesRegex(ValueError, r"No \[gitlab\] table"):
            gitlab_reviewer._load_gitlab_config({})

    def test_raises_when_mcp_table_or_command_is_missing(self):
        with self.assertRaisesRegex(ValueError, r"\[gitlab\.mcp\]"):
            gitlab_reviewer._load_gitlab_config({"gitlab": {}})

    def test_returns_normalized_config(self):
        config = {
            "gitlab": {"mcp": {"command": "uvx", "args": ["mcp-gitlab"]}},
        }

        result = gitlab_reviewer._load_gitlab_config(config)

        self.assertEqual(
            result,
            {"mcp": {"command": "uvx", "args": ["mcp-gitlab"], "env": {}}},
        )

    def test_defaults_args_to_empty_list(self):
        config = {"gitlab": {"mcp": {"command": "uvx"}}}

        result = gitlab_reviewer._load_gitlab_config(config)

        self.assertEqual(result["mcp"]["args"], [])

    def test_defaults_env_to_empty_dict(self):
        config = {"gitlab": {"mcp": {"command": "uvx"}}}

        result = gitlab_reviewer._load_gitlab_config(config)

        self.assertEqual(result["mcp"]["env"], {})

    def test_passes_through_configured_env_values(self):
        config = {
            "gitlab": {
                "mcp": {
                    "command": "uvx",
                    "env": {
                        "GITLAB_URL": "https://gitlab.example.com",
                        "GITLAB_TOKEN": "glpat-secret",
                    },
                },
            },
        }

        result = gitlab_reviewer._load_gitlab_config(config)

        self.assertEqual(
            result["mcp"]["env"],
            {
                "GITLAB_URL": "https://gitlab.example.com",
                "GITLAB_TOKEN": "glpat-secret",
            },
        )


class RunGitlabReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetches_the_mr_and_reviews_it_reporting_the_verdict(self):
        working_dir = Path("/project")
        mr_link = "https://gitlab.example.com/group/project/-/merge_requests/1"
        config = {
            "gitlab": {"mcp": {"command": "uvx", "args": []}},
            "docs_dir": "docs",
            "models": {"reviewer": "x", "gitlab_fetcher": "x"},
            "lint": [],
        }
        mr = {
            "title": "Fix thing",
            "description": "Details.",
            "diff": "--- a/f.py\n+++ b/f.py\n",
        }

        with (
            patch("meow.gitlab_reviewer.load_config", return_value=config),
            patch(
                "meow.gitlab_reviewer._fetch_merge_request",
                new=AsyncMock(return_value=mr),
            ) as mock_fetch,
            patch(
                "meow.gitlab_reviewer.ReviewerAgent.review_merge_request",
                new=AsyncMock(return_value=("PASS", "STATUS: PASS\n")),
            ) as mock_review,
        ):
            result = await gitlab_reviewer.run_gitlab_review(working_dir, mr_link)

        self.assertIsNone(result)
        mock_fetch.assert_awaited_once_with(
            working_dir,
            config,
            {"mcp": {"command": "uvx", "args": [], "env": {}}},
            mr_link,
        )
        mock_review.assert_awaited_once_with(
            mr["title"], mr["description"], mr["diff"]
        )

    async def test_raises_before_fetching_when_gitlab_config_is_missing(self):
        with (
            patch("meow.gitlab_reviewer.load_config", return_value={}),
            patch(
                "meow.gitlab_reviewer._fetch_merge_request", new=AsyncMock()
            ) as mock_fetch,
            self.assertRaisesRegex(ValueError, r"No \[gitlab\] table"),
        ):
            await gitlab_reviewer.run_gitlab_review(
                Path("/project"),
                "https://gitlab.example.com/group/project/-/merge_requests/1",
            )

        mock_fetch.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
