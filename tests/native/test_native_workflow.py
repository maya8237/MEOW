import json

from meow.cli.cli import cli_main
from meow.native.native_prompt import role_prompt
from meow.project.config import load_config
from meow.project.custom.rendering import effective_models


def test_native_workflow_prints_resolved_json(tmp_path, capsys):
    cli_main(["native", "workflow", "default", "--work-dir", str(tmp_path)])
    data = json.loads(capsys.readouterr().out)
    assert data["name"] == "default"
    assert data["scope"] == "builtin"
    assert data["steps"][0]["uses"] == "builtin.knowledge"
    assert data["fingerprint"]


def test_native_prompt_and_effective_models_honor_resolved_agent(tmp_path):
    directory = tmp_path / ".meow" / "agents"
    directory.mkdir(parents=True)
    (directory / "planner.toml").write_text(
        'description="Planner"\nmodel="y"\ntools=["Read"]\n'
        'prompt="Custom plan {{plan_file}}"\n',
        encoding="utf-8",
    )
    result = role_prompt(tmp_path, tmp_path, "planner", plan_file=tmp_path / "plan.md")
    assert result["system_prompt"] == f"Custom plan {tmp_path / 'plan.md'}"
    assert result["model"] == "y"
    assert result["tools"] == ["Read"]
    assert effective_models(load_config(tmp_path))["planner"] == "y"
    (directory.parent / "config.toml").write_text(
        '[models]\nplanner="x"', encoding="utf-8"
    )
    assert (
        role_prompt(tmp_path, tmp_path, "planner", plan_file=tmp_path / "plan.md")[
            "model"
        ]
        == "x"
    )


def test_native_reviewer_override_gets_contract_without_variants(tmp_path):
    directory = tmp_path / ".meow" / "agents"
    directory.mkdir(parents=True)
    (directory / "reviewer.toml").write_text(
        'description="Review"\nprompt="My review"\ntools=["Read", "Write"]',
        encoding="utf-8",
    )
    plan = tmp_path / "plan.md"
    plan.write_text("Plan", encoding="utf-8")
    result = role_prompt(tmp_path, tmp_path, "reviewer-plan", plan_file=plan)
    assert "My review" in result["system_prompt"]
    assert "STATUS: PASS" in result["system_prompt"]
