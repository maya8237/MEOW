from meow.shaping import *


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
