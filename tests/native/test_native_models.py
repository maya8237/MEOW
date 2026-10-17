"""Native model dispatch respects host capabilities without changing SDK config."""

import json
import subprocess

import pytest

from meow.agents.base import Agent, ProjectContext
from meow.native.native_custom import custom
from meow.native.native_prompt import PROMPT_ROLES, role_prompt
from meow.project.config import load_config


@pytest.mark.parametrize(
    ("host", "available", "expected"),
    [(None, None, "haiku"), ("codex", None, None), ("codex", ["haiku"], "haiku")],
)
def test_custom_delegate_routes_model_without_changing_config(
    tmp_path, host, available, expected
):
    directory = tmp_path / ".meow"
    directory.mkdir()
    (directory / "config.toml").write_text(
        '[custom]\nagents = ["delegates"]\n', encoding="utf-8"
    )
    delegates = tmp_path / "delegates"
    delegates.mkdir()
    (delegates / "audit.md").write_text(
        "---\nname: audit\ndescription: Audit code\nmodel: haiku\n---\nInspect.\n",
        encoding="utf-8",
    )

    result = custom(tmp_path, tmp_path, host=host, available_models=available)

    assert result["agents"][0]["model"] == expected
    assert result["agents"][0]["configured_model"] == "haiku"
    assert load_config(tmp_path)["customizations"].agents[0].model == "haiku"


def _configure(root, model):
    directory = root / ".meow"
    directory.mkdir(exist_ok=True)
    (directory / "config.toml").write_text(
        "[models]\n"
        + "\n".join(
            f"{role} = {json.dumps(model)}"
            for role in (
                "explorer",
                "planner",
                "generator",
                "reviewer",
                "review_fixer",
                "lint_fixer",
            )
        ),
        encoding="utf-8",
    )


def test_codex_inherits_instead_of_dispatching_configured_claude_model(tmp_path):
    _configure(tmp_path, "claude-haiku-4-5")

    result = role_prompt(tmp_path, tmp_path, "explorer", host="codex")

    assert result["model"] is None
    assert result["configured_model"] == "claude-haiku-4-5"
    assert result["host"] == "codex"
    assert result["model_resolution"] == "fallback"
    assert "claude-haiku-4-5" in result["model_fallback_reason"]
    assert "capabilities" in result["model_fallback_reason"]


@pytest.mark.parametrize("host", ["codex", "generic", "claude"])
def test_explicit_capabilities_select_only_supported_configured_id(tmp_path, host):
    _configure(tmp_path, "host-model")

    result = role_prompt(
        tmp_path,
        tmp_path,
        "explorer",
        host=host,
        available_models=["host-model"],
    )

    assert result["model"] == "host-model"
    assert result["configured_model"] == "host-model"
    assert result["model_resolution"] == "configured"
    assert result["model_fallback_reason"] is None


@pytest.mark.parametrize("host", ["codex", "generic", "claude"])
@pytest.mark.parametrize("available_models", [[], ["some-other-model"]])
def test_unavailable_model_inherits_with_reason(tmp_path, host, available_models):
    _configure(tmp_path, "unsupported")

    result = role_prompt(
        tmp_path,
        tmp_path,
        "explorer",
        host=host,
        available_models=available_models,
    )

    assert result["model"] is None
    assert result["configured_model"] == "unsupported"
    assert result["model_resolution"] == "fallback"
    assert "unsupported" in result["model_fallback_reason"]
    assert "unavailable" in result["model_fallback_reason"]


@pytest.mark.parametrize("host", ["codex", "generic", "claude", None])
def test_unconfigured_model_inherits_without_fallback(tmp_path, host):
    result = role_prompt(tmp_path, tmp_path, "review-fixer", host=host)

    assert result["model"] is None
    assert result["configured_model"] is None
    assert result["model_resolution"] == "inherit"
    assert result["model_fallback_reason"] is None


@pytest.mark.parametrize("host", ["claude", None])
def test_legacy_claude_models_preserved_even_in_codex_environment(
    tmp_path, monkeypatch, host
):
    monkeypatch.setenv("CODEX_THREAD_ID", "inherited-test-session")
    _configure(tmp_path, "haiku")

    result = role_prompt(tmp_path, tmp_path, "explorer", host=host)

    assert result["model"] == "haiku"
    assert result["configured_model"] == "haiku"
    assert result["host"] == "claude"


@pytest.mark.parametrize("role", PROMPT_ROLES)
def test_every_native_role_routes_models(tmp_path, role):
    _configure(tmp_path, "unavailable")
    plan = tmp_path / "plan.md"
    plan.write_text("# Plan\n", encoding="utf-8")
    if role == "reviewer-branch":
        subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(tmp_path),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "--allow-empty",
                "-m",
                "Initial",
            ],
            check=True,
            capture_output=True,
        )

    result = role_prompt(
        tmp_path,
        tmp_path,
        role,
        plan_file=plan,
        target="HEAD",
        branch="HEAD",
        host="generic",
    )

    assert result["model"] is None
    assert result["configured_model"] == "unavailable"
    assert result["model_resolution"] == "fallback"


def test_custom_role_model_routes_without_changing_sdk_lookup(tmp_path):
    directory = tmp_path / ".meow" / "agents"
    directory.mkdir(parents=True)
    (directory / "explorer.toml").write_text(
        'description="Explore"\nmodel="custom-claude-model"\n'
        'tools=["Read"]\nprompt="Inspect this project"\n',
        encoding="utf-8",
    )
    config = load_config(tmp_path)
    context = ProjectContext(tmp_path, config)
    definition = Agent(context).definition("explorer")

    result = role_prompt(tmp_path, tmp_path, "explorer", host="codex")

    assert result["system_prompt"] == "Inspect this project"
    assert result["model"] is None
    assert result["configured_model"] == "custom-claude-model"
    assert context.model("explorer", definition) == "custom-claude-model"
    assert config["models"]["explorer"] is None
    assert load_config(tmp_path)["models"] == config["models"]


def test_native_fallback_preserves_configured_sdk_model(tmp_path):
    _configure(tmp_path, "claude-haiku-4-5")

    result = role_prompt(tmp_path, tmp_path, "explorer", host="codex")
    config = load_config(tmp_path)
    context = ProjectContext(tmp_path, config)

    assert result["model"] is None
    assert config["models"]["explorer"] == "claude-haiku-4-5"
    assert context.model("explorer") == "claude-haiku-4-5"
