import pytest

from meow.project.custom.expressions import ExpressionError, evaluate, names


def test_boolean_logic_and_comparison():
    assert evaluate("not plan_given and round > 1", {"plan_given": False, "round": 2})
    assert not evaluate(
        "not plan_given and round > 1", {"plan_given": False, "round": 1}
    )


def test_dotted_lookup_and_string_equality():
    scope = {"steps": {"review": {"status": "PASS"}}}
    assert evaluate("steps.review.status == 'PASS'", scope) is True
    assert evaluate('steps.review.status != "FAIL"', scope) is True


def test_missing_dotted_key_is_none():
    assert evaluate("steps.security.status", {"steps": {}}) is None
    assert evaluate("not steps.security.status", {"steps": {}}) is True


def test_parentheses_and_precedence():
    scope = {"a": False, "b": True, "c": False}
    assert evaluate("(a or b) and not c", scope) is True
    assert evaluate("a or b and c", scope) is False


def test_literals():
    assert evaluate("round >= 3", {"round": 3}) is True
    assert evaluate("flag == true", {"flag": True}) is True
    assert evaluate("false", {}) is False


def test_unknown_identifier_is_an_error_naming_it():
    with pytest.raises(ExpressionError, match="plan_givn"):
        evaluate("plan_givn", {"plan_given": True})


@pytest.mark.parametrize(
    "text",
    ["__import__('os')", "a.b()", "a +", "a and", "(a", "a b", "'open", "a = 1", ""],
)
def test_rejects_anything_outside_the_grammar(text):
    with pytest.raises(ExpressionError):
        evaluate(text, {"a": 1, "b": 2})


def test_names_keeps_dotted_paths_whole():
    assert names("a.b and not (c == 'x') or round > 1") == {"a.b", "c", "round"}
