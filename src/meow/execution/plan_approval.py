"""Terminal prompt that lets a user approve a generated sprint plan."""

from pathlib import Path


def _print_plan(plan_file: Path) -> None:
    print(f"\n----- Sprint plan: {plan_file} -----\n")
    print(plan_file.read_text(encoding="utf-8"))
    print("----- end of plan -----\n")


def _prompt_plan_approval(plan_file: Path) -> bool:
    """Show the plan and ask the user to approve it before the generator
    runs -- the default `approve_plan` callback for --manually-approve-plan.
    EOF (no console attached to answer) is treated as declining, the same
    as any other unclear answer; only 'y'/'yes' approves."""
    _print_plan(plan_file)
    try:
        answer = input("Proceed with this plan? [y/N]: ")
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes"}
