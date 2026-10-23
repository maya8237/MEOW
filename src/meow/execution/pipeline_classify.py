"""Pure pipeline job classification, decided by job status only."""

import re
from dataclasses import dataclass

from meow.integrations.pipeline_api import PipelineJob, PipelineSnapshot

_REVIEW = re.compile(r"^meow[-_]review$")
_NEVER_FIXED = re.compile(r"deploy|release|publish|rollback", re.IGNORECASE)


@dataclass(frozen=True)
class Classification:
    running: bool
    review: PipelineJob | None
    review_failed: bool
    other_failed: tuple[PipelineJob, ...]
    fixable_other: tuple[PipelineJob, ...]


def is_review_job(job: PipelineJob) -> bool:
    return bool(_REVIEW.match(job.name))


def never_fixed(job: PipelineJob) -> bool:
    return bool(_NEVER_FIXED.search(job.name) or _NEVER_FIXED.search(job.stage))


def classify(snapshot: PipelineSnapshot, *, fix_non_meow: bool) -> Classification:
    jobs = snapshot.jobs
    review = next((job for job in jobs if is_review_job(job)), None)
    other_failed = tuple(
        job for job in jobs if job.status == "failed" and not is_review_job(job)
    )
    fixable = tuple(job for job in other_failed if not never_fixed(job))
    return Classification(
        running=any(job.status == "running" for job in jobs),
        review=review,
        review_failed=review is not None and review.status == "failed",
        other_failed=other_failed,
        fixable_other=fixable if fix_non_meow else (),
    )
