from builtins import input as input

_PATCHABLE = {
    "_boot_repo",
    "_ensure_clean_tree",
    "_is_linked_worktree",
    "_prompt_plan_approval",
    "load_config",
    "run_ci_review",
    "run_issue_solver",
    "run_lint_fix",
    "run_plan",
    "run_review_command",
    "run_sprint",
}


def _core():
    from importlib import import_module

    return import_module("meow.cli.cli")


def __getattr__(name):
    return getattr(_core(), name)


def _prompt_plan_approval(plan_file):
    """Facade wrapper that keeps the public input patch point working."""
    import builtins

    module = _core()
    module.input = input if input is not builtins.input else builtins.input
    return module._prompt_plan_approval(plan_file)


def cli_main(*args, **kwargs):
    """Run the CLI while keeping facade-level monkeypatches effective."""
    module = _core()
    for name in _PATCHABLE:
        if name in globals():
            setattr(module, name, globals()[name])
    module._prompt_plan_approval = _prompt_plan_approval
    return module.cli_main(*args, **kwargs)
