import unittest

from meow.integrations import github_reviewer


class LoadGithubConfigTests(unittest.TestCase):
    def test_raises_when_github_table_is_missing(self):
        with self.assertRaisesRegex(ValueError, r"No \[github\] table"):
            github_reviewer._load_github_config({})

    def test_raises_when_mcp_table_or_command_is_missing(self):
        with self.assertRaisesRegex(ValueError, r"\[github\.mcp\]"):
            github_reviewer._load_github_config({"github": {}})

    def test_returns_normalized_config(self):
        config = {
            "github": {"mcp": {"command": "uvx", "args": ["mcp-github"]}},
        }

        result = github_reviewer._load_github_config(config)

        self.assertEqual(
            result,
            {"mcp": {"command": "uvx", "args": ["mcp-github"], "env": {}}},
        )

    def test_defaults_args_to_empty_list(self):
        result = github_reviewer._load_github_config(
            {"github": {"mcp": {"command": "uvx"}}}
        )

        self.assertEqual(result["mcp"]["args"], [])

    def test_defaults_env_to_empty_dict(self):
        result = github_reviewer._load_github_config(
            {"github": {"mcp": {"command": "uvx"}}}
        )

        self.assertEqual(result["mcp"]["env"], {})

    def test_passes_through_configured_env_values(self):
        config = {
            "github": {
                "mcp": {
                    "command": "uvx",
                    "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp-secret"},
                },
            },
        }

        result = github_reviewer._load_github_config(config)

        self.assertEqual(
            result["mcp"]["env"], {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp-secret"}
        )


if __name__ == "__main__":
    unittest.main()
