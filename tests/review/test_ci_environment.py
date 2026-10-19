"""Execute the shipped CI environment setup, without installing or reviewing."""

import json
import os
import shlex
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


def shell():
    bash = shutil.which("bash")
    bundled = Path("C:/Program Files/Git/bin/bash.exe")
    if bash or bundled.is_file():
        return bash or str(bundled)
    pytest.skip("POSIX CI shell unavailable")


def environment(values, *, fallback=False, reuse=False):
    template = Path(__file__).resolve().parents[2] / "templates/gitlab-ci-review.yml"
    block = template.read_text(encoding="utf-8").split(
        "  before_script:\n    - |\n", 1
    )[1]
    setup = textwrap.dedent(block.split("\n    - ", 1)[0])
    if fallback or reuse:
        guide = (
            template.parents[1] / "skills/integrate-ci-review/references/environment.md"
        )
        language = "bash" if reuse else "sh"
        setup = (
            guide
            .read_text(encoding="utf-8")
            .split(f"```{language}\n", 1)[1]
            .split("```", 1)[0]
            + setup
        )
    names = [
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    ]
    inherited = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ANTHROPIC_", "MEOW_")) and key != "LITELLM_API_TOKEN"
    }
    probe = (
        "import os,json; print(json.dumps({k:os.environ[k] for k in "
        + repr(names)
        + " if k in os.environ}))"
    )
    command = (
        shlex.quote(sys.executable.replace("\\", "/")) + " -c " + shlex.quote(probe)
    )
    result = subprocess.run(
        [shell(), "-c", setup + "\n" + command],
        env=inherited | values,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_meow_credentials_and_models_reach_claude():
    assert environment({
        "MEOW_API_URL": "https://proxy.example.invalid",
        "MEOW_API_TOKEN": "fake-token",
        "MEOW_API_KEY": "fake-key",
        "MEOW_MODEL": "custom-primary",
        "MEOW_DEFAULT_SONNET_MODEL": "custom-sonnet",
        "MEOW_DEFAULT_OPUS_MODEL": "custom-opus",
        "MEOW_DEFAULT_HAIKU_MODEL": "custom-haiku",
    }) == {
        "ANTHROPIC_BASE_URL": "https://proxy.example.invalid",
        "ANTHROPIC_AUTH_TOKEN": "fake-token",
        "ANTHROPIC_API_KEY": "fake-key",
        "ANTHROPIC_MODEL": "custom-primary",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "custom-sonnet",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "custom-opus",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "custom-haiku",
    }


def test_native_environment_still_works_and_no_values_are_invented():
    assert environment({"ANTHROPIC_API_KEY": "native-key"}) == {
        "ANTHROPIC_API_KEY": "native-key"
    }
    assert environment({}) == {}


def test_meow_environment_overrides_native_values():
    assert environment({
        "MEOW_DEFAULT_SONNET_MODEL": "override",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "native",
    }) == {
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "override",
    }


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({}, "approved-model-id"),
        ({"MEOW_DEFAULT_SONNET_MODEL": "ci-override"}, "ci-override"),
        ({"ANTHROPIC_DEFAULT_SONNET_MODEL": "native-override"}, "native-override"),
        ({"MEOW_DEFAULT_SONNET_MODEL": ""}, ""),
    ],
)
def test_approved_model_fallback_only_applies_when_environment_absent(values, expected):
    assert (
        environment(values, fallback=True)["ANTHROPIC_DEFAULT_SONNET_MODEL"] == expected
    )


def test_direct_api_key_does_not_require_proxy_or_settings():
    assert environment({"MEOW_API_KEY": "fake-key"}) == {
        "ANTHROPIC_API_KEY": "fake-key"
    }


def test_environment_values_remain_literal_data():
    value = "model$(printf unexpected)"
    assert environment({"MEOW_MODEL": value}) == {"ANTHROPIC_MODEL": value}


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (
            {"LITELLM_API_TOKEN": "existing-ci-token"},
            {"ANTHROPIC_AUTH_TOKEN": "existing-ci-token"},
        ),
        (
            {"LITELLM_API_TOKEN": "existing-ci-token", "MEOW_API_TOKEN": "override"},
            {"ANTHROPIC_AUTH_TOKEN": "override"},
        ),
        (
            {"LITELLM_API_TOKEN": "existing-ci-token", "MEOW_API_TOKEN": ""},
            {"ANTHROPIC_AUTH_TOKEN": ""},
        ),
        ({}, {}),
    ],
)
def test_existing_ci_token_alias_preserves_explicit_meow_values(values, expected):
    assert environment(values, reuse=True) == expected
