"""The pipeline verification docs and skills stay linked, honest, and short."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIMIT = 200


def _lines(*parts: str) -> list[str]:
    return ROOT.joinpath(*parts).read_text(encoding="utf-8").splitlines()


def _text(*parts: str) -> str:
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def test_pipeline_doc_under_limit_and_linked():
    assert len(_lines("docs", "PIPELINE.md")) < LIMIT
    assert "PIPELINE.md" in _text("docs", "CLI.md")
    assert len(_lines("docs", "CLI.md")) <= LIMIT
    run_skill = _text("skills", "run", "SKILL.md")
    assert "Verified by pipeline" in run_skill
    assert "--fix-non-meow-jobs" in run_skill
    assert "PIPELINE.md" in run_skill
    assert len(run_skill.splitlines()) < LIMIT


def test_native_guidance_matches_the_run_skill():
    for parts in (
        ("skills", "_shared", "native-mode.md"),
        ("skills", "run", "references", "native.md"),
    ):
        assert len(_lines(*parts)) < LIMIT
        text = _text(*parts)
        assert "final delivery" in text
        assert "Verified by pipeline" in text or "verified by the pipeline" in text
