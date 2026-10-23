"""Pure classification of pipeline jobs for final-delivery verification."""

from meow.execution.pipeline_classify import classify, is_review_job, never_fixed
from meow.integrations.pipeline_api import PipelineJob, PipelineSnapshot


def job(name, status="success", *, stage="test", allow_failure=False):
    return PipelineJob(name, name, stage, status, allow_failure, f"https://ci/{name}")


def snap(*jobs, status="success"):
    return PipelineSnapshot("1", status, "https://ci/1", tuple(jobs))


def test_running_when_any_job_pending():
    result = classify(snap(job("a"), job("b", "running")), fix_non_meow=False)
    assert result.running is True
    assert classify(snap(job("a")), fix_non_meow=False).running is False


def test_allow_failure_review_job_counts_as_failed():
    review = job("meow-review", "failed", allow_failure=True)
    result = classify(snap(review, status="success"), fix_non_meow=False)
    assert result.review == review
    assert result.review_failed is True
    assert result.other_failed == ()


def test_snapshot_success_does_not_hide_failed_review():
    result = classify(
        snap(job("meow_review", "failed"), status="success"), fix_non_meow=True
    )
    assert result.review_failed is True


def test_review_job_name_variants():
    assert is_review_job(job("meow-review"))
    assert is_review_job(job("meow_review"))
    assert not is_review_job(job("meow-reviewer"))
    assert not is_review_job(job("Meow-Review"))


def test_other_failed_listed_but_not_fixable_by_default():
    bad = job("unit", "failed")
    result = classify(snap(bad, job("meow-review")), fix_non_meow=False)
    assert result.other_failed == (bad,)
    assert result.fixable_other == ()
    assert result.review_failed is False
    assert classify(snap(bad), fix_non_meow=True).fixable_other == (bad,)


def test_deploy_never_fixable_even_with_flag():
    deploy = job("deploy-prod", "failed")
    staged = job("ship", "failed", stage="Deploy")
    unit = job("unit", "failed")
    result = classify(snap(deploy, staged, unit), fix_non_meow=True)
    assert result.other_failed == (deploy, staged, unit)
    assert result.fixable_other == (unit,)
    assert never_fixed(job("x", stage="Release"))
    assert never_fixed(job("publish-docs"))
    assert never_fixed(job("rollback"))
    assert not never_fixed(job("unit"))


def test_skipped_and_canceled_jobs_are_not_failures():
    jobs = (job("a", "skipped"), job("meow-review", "canceled"), job("c", "success"))
    result = classify(snap(*jobs, status="failed"), fix_non_meow=True)
    assert result.other_failed == ()
    assert result.review_failed is False
    assert result.running is False


def test_later_failed_review_job_is_a_review_failure():
    passing = job("meow-review", "success")
    failing = job("meow_review", "failed", stage="review")
    result = classify(snap(passing, failing), fix_non_meow=False)
    assert result.review_failed is True
    assert result.review.name == "meow_review"
    assert result.other_failed == ()


def test_first_review_job_used_when_all_pass():
    first, second = job("meow-review"), job("meow_review")
    result = classify(snap(first, second), fix_non_meow=False)
    assert result.review_failed is False
    assert result.review == first


def test_failed_review_job_preferred_over_passing_first():
    failing = job("meow-review", "failed")
    result = classify(snap(failing, job("meow_review")), fix_non_meow=False)
    assert result.review == failing
