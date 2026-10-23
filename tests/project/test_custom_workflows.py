from dataclasses import replace

import pytest

from meow.project.custom.definitions import discover
from meow.project.custom.sources import CustomSource, builtin_sources
from meow.project.custom.workflows import expand, fingerprint, load_workflow, validate


def catalog(tmp_path, text):
    directory = tmp_path / "workflows"
    directory.mkdir(exist_ok=True)
    path = directory / "custom.toml"
    path.write_text('description = "Custom"\n' + text, encoding="utf-8")
    return discover([
        CustomSource("workflows", directory, "project", None),
        *builtin_sources(),
    ])


def test_shipped_workflows_validate_and_quick_omits_preplan(tmp_path):
    definitions = discover(builtin_sources())
    for name in ("default", "quick", "review-and-fix"):
        assert validate(definitions.workflow(name), definitions) == []
    quick = expand(definitions.workflow("quick"), definitions)
    assert "builtin.knowledge" not in {step.target for step in quick}
    assert "builtin.shape" not in {step.target for step in quick}


@pytest.mark.parametrize(
    "body,problem",
    [
        ('[[steps]]\nid = "a"\nagent = "missing"', "missing"),
        ('[[steps]]\nid = "a"\nuses = "builtin.nope"', "nope"),
        (
            '[[steps]]\nid = "a"\nagent = "explorer"\n'
            '[[steps]]\nid = "a"\nagent = "explorer"',
            "duplicate",
        ),
        ('[[steps]]\nid = "a"\ncall = "custom"', "cycle"),
        ('[[steps]]\nid = "a"\nloop = [{id="b", agent="explorer"}]', "blocking"),
        ('[[steps]]\nid = "a"\nparallel = [{id="b", agent="generator"}]', "parallel"),
        (
            '[[steps]]\nid = "a"\nparallel = [{id="b", uses="builtin.review"}]',
            "parallel",
        ),
        ('[[steps]]\nid = "a"\nagent = "explorer"\ntask = "{{typo}}"', "typo"),
        ('[[steps]]\nid = "a"\nagent = "explorer"\nwhen = "__import__(x)"', "when"),
        ('[[steps]]\nid = "a"\nagent = "explorer"\nwhen = "typo"', "typo"),
    ],
)
def test_validation_rejects_unsafe_or_unresolved_steps(tmp_path, body, problem):
    definitions = catalog(tmp_path, body)
    assert any(
        problem in message
        for message in validate(definitions.workflow("custom"), definitions)
    )


def test_gate_defaults_depend_on_enclosing_loop(tmp_path):
    definitions = catalog(
        tmp_path,
        '[[steps]]\nid = "outside"\ngate = "reviewer"\n'
        '[[steps]]\nid = "rounds"\nloop = [{id="inside",gate="reviewer"}]',
    )
    workflow = definitions.workflow("custom")
    assert workflow.steps[0].blocks == "delivery"
    assert workflow.steps[1].body[0].blocks == "loop"


def test_fingerprint_ignores_line_ending_style(tmp_path):
    definitions = catalog(tmp_path, '[[steps]]\nid="audit"\nagent="explorer"')
    workflow = definitions.workflow("custom")
    before = fingerprint(workflow, definitions)
    text = definitions.agent("explorer").file.read_bytes().replace(b"\r\n", b"\n")
    path = tmp_path / "explorer.toml"
    path.write_bytes(text.replace(b"\n", b"\r\n"))
    definitions = replace(
        definitions,
        catalog=tuple(
            replace(item, file=path) if item.name == "explorer" else item
            for item in definitions.catalog
        ),
    )
    assert fingerprint(workflow, definitions) == before


def test_fingerprint_tracks_used_agent_and_called_workflow(tmp_path):
    definitions = catalog(tmp_path, '[[steps]]\nid="audit"\nagent="explorer"')
    workflow = definitions.workflow("custom")
    before = fingerprint(workflow, definitions)
    agent = definitions.agent("explorer")
    path = tmp_path / "explorer.toml"
    path.write_bytes(agent.file.read_bytes() + b"\n# changed\n")
    definitions = replace(
        definitions,
        catalog=tuple(
            replace(item, file=path) if item.name == "explorer" else item
            for item in definitions.catalog
        ),
    )
    assert fingerprint(workflow, definitions) != before


@pytest.mark.parametrize(
    "body",
    [
        '[[steps]]\nagent="explorer"',
        '[[steps]]\nid="a"\nagent="explorer"\ngate="reviewer"',
        '[[steps]]\nid="a"\nagent="explorer"\ntimeout=-1',
        '[[steps]]\nid="a"\nagent="explorer"\ntimeout=nan',
        '[[steps]]\nid="a"\ngate="reviewer"\nblocks="unknown"',
        '[[steps]]\nid="a"\ngate="reviewer"\nblocks=[]',
        '[[steps]]\nid="a"\nloop=[]\nmax_rounds=0',
        '[[steps]]\nid="a"\nagent="explorer"\ntypo=true',
    ],
)
def test_bad_step_schema_names_file(tmp_path, body):
    path = tmp_path / "bad.toml"
    path.write_text('description="Bad"\n' + body, encoding="utf-8")
    source = CustomSource("workflows", tmp_path, "project", None)
    with pytest.raises(ValueError, match=r"bad\.toml"):
        load_workflow(path, source)


def test_calls_are_expanded_with_outer_condition_preserved(tmp_path):
    definitions = catalog(tmp_path, '[[steps]]\nid="nested"\ncall="quick"\nwhen="test"')
    workflow = definitions.workflow("custom")
    steps = expand(workflow, definitions)
    assert all(step.kind != "call" for step in steps)
    assert all("test" in step.when for step in steps)


def test_fingerprint_includes_configured_compatibility_delegates(tmp_path):
    from meow.project.config import load_config

    directory = tmp_path / "custom-agents"
    directory.mkdir()
    helper = directory / "helper.toml"
    helper.write_text(
        'description="Helper"\nprompt="First"\ntools=["Read"]', encoding="utf-8"
    )
    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow" / "config.toml").write_text(
        '[custom]\nagents=["custom-agents"]', encoding="utf-8"
    )
    catalog = load_config(tmp_path)["customizations"]
    before = fingerprint(catalog.workflow("default"), catalog)
    helper.write_text(
        helper.read_text(encoding="utf-8").replace("First", "Second"), encoding="utf-8"
    )
    catalog = load_config(tmp_path)["customizations"]
    assert fingerprint(catalog.workflow("default"), catalog) != before


def test_workflow_flags_default_and_parse(tmp_path):
    plain = catalog(tmp_path, '[[steps]]\nid = "a"\nuses = "builtin.generate"')
    workflow = plain.workflow("custom")
    assert workflow.auto_test is True and workflow.short_plan is False
    custom = catalog(
        tmp_path,
        'auto_test = false\nshort_plan = true\n[[steps]]\nid = "a"\n'
        'uses = "builtin.generate"',
    )
    workflow = custom.workflow("custom")
    assert workflow.auto_test is False and workflow.short_plan is True


def test_workflow_flags_must_be_booleans(tmp_path):
    with pytest.raises(ValueError, match="auto_test must be true or false"):
        catalog(
            tmp_path, 'auto_test = "no"\n[[steps]]\nid = "a"\nuses = "builtin.generate"'
        )
