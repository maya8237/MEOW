"""Onboarding options each release announces after `meow --update`.

`meow --update` runs the previous release, so it reads this table from the new
release with `python -m meow.project.announcements` instead of importing it.
Tag a new `/meow:onboard` step with "unreleased"; the release stamps its version.
Keep this module stdlib-only so that subprocess stays fast.
"""

import json

# `_GAPS` key -> (release that added the onboarding step, one-line summary).
ANNOUNCED: dict[str, tuple[str, str]] = {
    "delivery": (
        "0.1.19",
        "Default PR/MR target branch ([delivery].target_branch); "
        "--target still overrides it per run.",
    ),
    "tester_write_tests": (
        "0.1.19",
        "Let the tester add new tests for the change ([tester] write_tests).",
    ),
    "tester_browser_launch": (
        "unreleased",
        "Select the tester's browser ([tester] browser_channel, browser_executable "
        "or browser_playwright) and check it with `meow native verify --preflight`.",
    ),
}

# `_GAPS` keys added after 0.1.18 that announce nothing: they predate their gap
# entry in `/meow:onboard`, or the skill offers them only from the gap checklist.
NOT_ANNOUNCED = frozenset({
    "lint",
    "models",
    "orchestrator",
    "tester_dev_server",
    "workflow",
})


def main() -> None:
    print(
        json.dumps({
            key: {"since": since, "summary": summary}
            for key, (since, summary) in ANNOUNCED.items()
        })
    )


if __name__ == "__main__":
    main()
