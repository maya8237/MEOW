import unittest

from meow.integrations import gitlab_reviewer


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


if __name__ == "__main__":
    unittest.main()
