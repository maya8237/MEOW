from . import core as _core
from .core import (
    _BRANCH_UNSAFE as _BRANCH_UNSAFE,
)
from .core import (
    DEFAULT_BRANCH_PREFIX as DEFAULT_BRANCH_PREFIX,
)
from .core import (
    Callable as Callable,
)
from .core import (
    IssueFetcherAgent as IssueFetcherAgent,
)
from .core import (
    IssueUnresolvedError as IssueUnresolvedError,
)
from .core import (
    Path as Path,
)
from .core import (
    ProjectContext as ProjectContext,
)
from .core import (
    _ensure_branch_worktree as _ensure_branch_worktree,
)
from .core import (
    _fetch_issue as _fetch_issue,
)
from .core import (
    _load_jira_config as _load_jira_config,
)
from .core import (
    _sanitize as _sanitize,
)
from .core import (
    _solve_issue as _solve_issue,
)
from .core import (
    get_logger as get_logger,
)
from .core import (
    json as json,
)
from .core import (
    load_config as load_config,
)
from .core import (
    logger as logger,
)
from .core import (
    re as re,
)
from .core import (
    run_issue_solver as run_issue_solver,
)
from .core import (
    run_sprint as run_sprint,
)
from .core import (
    tempfile as tempfile,
)

_run_issue_solver_impl = _core.run_issue_solver


async def run_issue_solver(*args, **kwargs):
    import sys

    facade = sys.modules[__name__]
    for name in (
        "load_config",
        "_fetch_issue",
        "_ensure_branch_worktree",
        "run_sprint",
    ):
        if hasattr(facade, name):
            setattr(_core, name, getattr(facade, name))
    return await _run_issue_solver_impl(*args, **kwargs)
