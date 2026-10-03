from importlib import import_module

_ALIASES = {
    "background": "meow.infrastructure.background",
    "branch_reviewer": "meow.integrations.branch_reviewer",
    "cli": "meow.cli",
    "gitlab_reviewer": "meow.integrations.gitlab_reviewer",
    "issue_solver": "meow.integrations.issue_solver",
    "lint_fix": "meow.infrastructure.lint_fix",
    "native": "meow.native",
    "native_cli": "meow.native.native_cli",
    "orchestrator": "meow.execution.orchestrator",
    "plan_files": "meow.project.plan_files",
    "prompts": "meow.project.prompts",
    "review_cli": "meow.cli.review_cli",
    "sprint": "meow.execution.sprint",
    "sprint_runner": "meow.execution.sprint_runner",
    "worktree": "meow.infrastructure.worktree",
}


def __getattr__(name):
    target = _ALIASES.get(name)
    if target is None:
        raise AttributeError(name)
    module = import_module(target)
    globals()[name] = module
    return module
