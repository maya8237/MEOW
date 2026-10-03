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
    print(f"\n----- Sprint plan: {plan_file} -----\n")
    print(plan_file.read_text(encoding="utf-8"))
    print("----- end of plan -----\n")
    try:
        answer = input("Proceed with this plan? [y/N]: ")
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes"}


def cli_main(*args, **kwargs):
    """Run the CLI while keeping facade-level monkeypatches effective."""
    module = _core()
    for name in _PATCHABLE:
        if name in globals():
            setattr(module, name, globals()[name])
    return module.cli_main(*args, **kwargs)
