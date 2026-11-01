from pathlib import Path

from meow.execution.mr_hook import mr_payload, problems


def test_flags_missing_mr():
    payload = mr_payload("run", {"pr": None, "pr_error": "no token"}, id="r")
    assert "not posted: no token" in problems(payload, Path("."))[0]


def test_flags_unverified_pipeline():
    delivery = {
        "pr": {"url": "u"},
        "pipeline": {"status": "not_verified", "failing": ["lint"], "detail": "x"},
    }
    found = problems(mr_payload("run", delivery), Path("."))
    assert "not verified: x (failing: lint)" in found[0]


def test_passes_verified():
    delivery = {"pr": {"url": "u"}, "pipeline": {"status": "verified"}}
    assert problems(mr_payload("run", delivery), Path(".")) == []
