import pytest

from meow.project.custom.templates import TemplateError, fold, render, template_names


def test_substitutes_values_verbatim_including_windows_paths():
    path = r"C:\Users\a b\plan.md"
    assert render("Write to {{plan_file}}.", {"plan_file": path}) == (
        f"Write to {path}."
    )


def test_dotted_substitution_and_none_renders_empty():
    variables = {"shape": {"approach": "adapter"}, "focus": None}
    assert render("[{{shape.approach}}][{{focus}}]", variables) == "[adapter][]"


def test_if_else_and_unless():
    text = (
        "{{#if bug_mode}}bug{{else}}feature{{/if}}{{#unless test}} no-test{{/unless}}"
    )
    assert render(text, {"bug_mode": True, "test": False}) == "bug no-test"
    assert render(text, {"bug_mode": False, "test": True}) == "feature"


def test_if_takes_an_expression():
    text = "{{#if round > 1 and not plan_given}}fix{{/if}}"
    assert render(text, {"round": 2, "plan_given": False}) == "fix"


def test_nested_blocks():
    text = "{{#if a}}A{{#if b}}B{{else}}b{{/if}}{{/if}}"
    assert render(text, {"a": True, "b": False}) == "Ab"


def test_fragment_includes_render_with_the_same_variables():
    fragments = {"arch": "check {{target}}"}
    assert render("Then {{> arch}}.", {"target": "seams"}, fragments=fragments) == (
        "Then check seams."
    )


def test_unknown_variable_names_the_file_and_variable():
    with pytest.raises(TemplateError, match=r"planner\.toml.*plan_fiel"):
        render("{{plan_fiel}}", {"plan_file": "p"}, where="planner.toml")


def test_unclosed_block_is_an_error():
    with pytest.raises(TemplateError, match="unclosed"):
        render("{{#if a}}open", {"a": True})


def test_stray_close_is_an_error():
    with pytest.raises(TemplateError):
        render("x{{/if}}", {})


def test_unknown_fragment_is_an_error():
    with pytest.raises(TemplateError, match="arch"):
        render("{{> arch}}", {}, fragments={})


def test_recursive_fragment_is_an_error():
    with pytest.raises(TemplateError, match="loop"):
        render("{{> loop}}", {}, fragments={"loop": "{{> loop}}"})


def test_fold_joins_paragraph_lines_and_keeps_structure():
    text = "a\nb\n\n- c\n- d\n\n# Head\n  indented\ne\nf"
    assert fold(text) == "a b\n\n- c\n- d\n\n# Head\n  indented\ne f"


def test_render_folds_template_text_but_not_values():
    rendered = render("one\ntwo {{value}}\n", {"value": "x\ny"})
    assert rendered == "one two x\ny"


def test_template_names_includes_blocks_and_fragments():
    fragments = {"arch": "{{target}}"}
    text = "{{plan_file}}{{#if bug_mode and steps.review.status}}{{> arch}}{{/if}}"
    assert template_names(text, fragments=fragments) == {
        "plan_file",
        "bug_mode",
        "steps.review.status",
        "target",
    }
