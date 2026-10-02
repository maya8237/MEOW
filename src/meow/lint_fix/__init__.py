from . import core as _core
from .core import (
    LintFixAgent as LintFixAgent,
)
from .core import (
    LintFixError as LintFixError,
)
from .core import (
    Path as Path,
)
from .core import (
    ProjectContext as ProjectContext,
)
from .core import (
    RunStore as RunStore,
)
from .core import (
    _fix_until_clean as _fix_until_clean,
)
from .core import (
    _report_only as _report_only,
)
from .core import (
    apply_lint_fixes as apply_lint_fixes,
)
from .core import (
    check_lint_commands as check_lint_commands,
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
    run_lint_fix as run_lint_fix,
)

_run_lint_fix_impl = _core.run_lint_fix


async def run_lint_fix(*args, **kwargs):
    import sys

    facade = sys.modules[__name__]
    for name in (
        "load_config",
        "check_lint_commands",
        "apply_lint_fixes",
        "LintFixAgent",
        "_fix_until_clean",
    ):
        if hasattr(facade, name):
            setattr(_core, name, getattr(facade, name))
    return await _run_lint_fix_impl(*args, **kwargs)


from .core import (
    subprocess as subprocess,
)
