from . import core as _core
from .core import (
    _UNRESUMABLE_FLAVOR_MESSAGES as _UNRESUMABLE_FLAVOR_MESSAGES,
)
from .core import (
    Path as Path,
)
from .core import (
    ProjectContext as ProjectContext,
)
from .core import (
    ReviewerAgent as ReviewerAgent,
)
from .core import (
    RunStore as RunStore,
)
from .core import (
    _branch_review as _branch_review,
)
from .core import (
    _detect_review_flavor as _detect_review_flavor,
)
from .core import (
    _ensure_existing_branch_worktree as _ensure_existing_branch_worktree,
)
from .core import (
    _fetch_issue as _fetch_issue,
)
from .core import (
    _fetch_merge_request as _fetch_merge_request,
)
from .core import (
    _gitlab_review as _gitlab_review,
)
from .core import (
    _jira_review_prompt as _jira_review_prompt,
)
from .core import (
    _latest_plan_file as _latest_plan_file,
)
from .core import (
    _load_gitlab_config as _load_gitlab_config,
)
from .core import (
    _load_jira_config as _load_jira_config,
)
from .core import (
    _plan_or_prompt_review as _plan_or_prompt_review,
)
from .core import (
    _plan_review as _plan_review,
)
from .core import (
    _prompt_review as _prompt_review,
)
from .core import (
    _raise_if_not_passed as _raise_if_not_passed,
)
from .core import (
    _require_branch_checked_out as _require_branch_checked_out,
)
from .core import (
    _resume_review_file as _resume_review_file,
)
from .core import (
    _review_sources as _review_sources,
)
from .core import (
    _run_prompt_fix_rounds as _run_prompt_fix_rounds,
)
from .core import (
    _run_review_rounds as _run_review_rounds,
)
from .core import (
    _sanitize as _sanitize,
)
from .core import (
    _validate_review_flags as _validate_review_flags,
)
from .core import (
    _verdict_status as _verdict_status,
)
from .core import (
    build_sprint as build_sprint,
)
from .core import (
    describe_lint_plan as describe_lint_plan,
)
from .core import (
    get_logger as get_logger,
)
from .core import (
    load_config as load_config,
)
from .core import (
    logger as logger,
)
from .core import (
    review_then_test as review_then_test,
)
from .core import (
    run_review_command as run_review_command,
)
from .core import (
    subprocess as subprocess,
)

_run_review_command_impl = _core.run_review_command


async def run_review_command(*args, **kwargs):
    import sys

    facade = sys.modules[__name__]
    for name in (
        "load_config",
        "ReviewerAgent",
        "_fetch_issue",
        "_fetch_merge_request",
        "_ensure_existing_branch_worktree",
        "_run_review_rounds",
        "review_then_test",
        "build_sprint",
        "Generator",
        "run_final_checks",
        "_require_branch_checked_out",
        "_prompt_review",
        "_plan_review",
    ):
        if hasattr(facade, name):
            setattr(_core, name, getattr(facade, name))
    return await _run_review_command_impl(*args, **kwargs)
