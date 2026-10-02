# Keep facade-level patching working for integrations and legacy callers.
from . import core as _core
from .core import (
    Callable as Callable,
)
from .core import (
    Path as Path,
)
from .core import (
    PlannerAgent as PlannerAgent,
)
from .core import (
    PlanNotApprovedError as PlanNotApprovedError,
)
from .core import (
    RunStore as RunStore,
)
from .core import (
    ShapeContext as ShapeContext,
)
from .core import (
    _latest_plan_file as _latest_plan_file,
)
from .core import (
    _prepare_sprint as _prepare_sprint,
)
from .core import (
    _run_review_rounds as _run_review_rounds,
)
from .core import (
    _run_rounds as _run_rounds,
)
from .core import (
    asdict as asdict,
)
from .core import (
    completion_ready as completion_ready,
)
from .core import (
    config_fingerprint as config_fingerprint,
)
from .core import (
    configured_checks as configured_checks,
)
from .core import (
    deliver_verified_run as deliver_verified_run,
)
from .core import (
    describe_lint_plan as describe_lint_plan,
)
from .core import (
    get_logger as get_logger,
)
from .core import (
    hashlib as hashlib,
)
from .core import (
    load_shape_artifact as load_shape_artifact,
)
from .core import (
    logger as logger,
)
from .core import (
    run_final_checks as run_final_checks,
)
from .core import (
    run_plan as run_plan,
)
from .core import (
    run_sprint as run_sprint,
)

_run_sprint_impl = _core.run_sprint


async def run_sprint(*args, **kwargs):
    import sys

    facade = sys.modules[__name__]
    for name in (
        "_prepare_sprint",
        "_run_review_rounds",
        "_run_rounds",
        "PlannerAgent",
        "run_final_checks",
        "deliver_verified_run",
    ):
        if hasattr(facade, name):
            setattr(_core, name, getattr(facade, name))
    return await _run_sprint_impl(*args, **kwargs)


from .core import (
    subprocess as subprocess,
)
