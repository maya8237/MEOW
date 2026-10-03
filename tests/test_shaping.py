from meow.project.shaping import (
    BreadboardArtifact,
    assess_request,
    load_shape_artifact,
    reflect_breadboard,
    save_shape_artifact,
)


def test_clear_requests_do_not_require_shaping():
    assert not assess_request("Add a button to settings").recommended
    assert assess_request(
        "Build a dashboard platform across multiple components"
    ).recommended


def test_breadboard_round_trip_and_reflection(tmp_path):
    artifact = BreadboardArtifact(("home",), ("save",), (), ("home to save",))
    path = tmp_path / "shape.json"
    save_shape_artifact(path, artifact)
    assert load_shape_artifact(path) == artifact
    assert reflect_breadboard(artifact) == ("missing connections",)


def test_reflection_names_missing_authorization_and_error_paths():
    artifact = BreadboardArtifact(
        ("settings", "service"),
        ("delete account",),
        ("settings -> service",),
        ("delete account and verify response",),
    )
    findings = reflect_breadboard(artifact)
    assert any(
        "delete account" in item and "authorization" in item for item in findings
    )
    assert any("delete account" in item and "error" in item for item in findings)


def test_reflection_names_unwired_place_and_untestable_slice():
    artifact = BreadboardArtifact(
        ("settings", "service"),
        ("save",),
        ("settings -> unknown",),
        ("save",),
    )
    findings = reflect_breadboard(artifact)
    assert any("unknown" in item and "wiring" in item for item in findings)
    assert any("save" in item and "verifiable" in item for item in findings)
