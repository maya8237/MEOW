"""Durable objective review bindings across helper invocations."""

from meow.execution.orchestration import review_evidence
from meow.execution.run_state import CheckResult
from meow.infrastructure.checks import (
    check_identity,
    code_revision,
    config_fingerprint,
    configured_checks,
)
from meow.project.config import load_config
from tests.orchestration.fakes import make_repository


def test_binding_survives_config_reload_and_legacy_review_report(tmp_path):
    repo, _ = make_repository(tmp_path, 'docs_dir = "docs/plans"\n')
    config = load_config(repo)
    config["_run_journal"] = object()
    binding = review_evidence.capture(repo, repo, config, [])
    report = repo / "docs" / "plans" / "branch.12345678.review.md"
    report.parent.mkdir(parents=True)
    report.write_text("SUMMARY: good\nSTATUS: PASS\n")
    assert review_evidence.current(repo, repo, load_config(repo), binding)
    (report.parent / "architecture.md").write_text("unreviewed source docs")
    assert not review_evidence.current(repo, repo, load_config(repo), binding)


def test_malformed_required_check_evidence_fails_closed(tmp_path):
    repo, _ = make_repository(tmp_path, '[[lint]]\ncommand = "true"\n')
    config = load_config(repo)
    binding = review_evidence.capture(repo, repo, config, [])
    binding["checks"] = [None]
    assert not review_evidence.current(repo, repo, config, binding)


def test_required_check_binding_survives_report_but_not_missing_identity(tmp_path):
    repo, _ = make_repository(
        tmp_path, 'docs_dir = "docs/plans"\n[[lint]]\ncommand = "true"\n'
    )
    config = load_config(repo)
    check = configured_checks(config)[0]
    result = CheckResult(
        "lint",
        check.command,
        True,
        0,
        0.1,
        "",
        code_revision(repo),
        config_fingerprint(repo),
        identity=check_identity(check),
    )
    binding = review_evidence.capture(repo, repo, config, [result])
    report = repo / "docs" / "plans" / "branch.12345678.review.md"
    report.parent.mkdir(parents=True)
    report.write_text("SUMMARY: good\nSTATUS: PASS\n")
    assert review_evidence.current(repo, repo, load_config(repo), binding)
    binding["checks"][0]["identity"] = "different-command"
    assert not review_evidence.current(repo, repo, load_config(repo), binding)
