"""Early stop for loops whose rounds keep failing the same way."""

import re


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _verdict_signature(verdict: str) -> frozenset[str]:
    lines = [line.strip() for line in verdict.splitlines()]
    findings = {
        _collapse(line)
        for line in lines
        if "FAIL" in line and not line.upper().startswith("STATUS:")
    }
    if findings:
        return frozenset(findings)
    summary = next((line for line in lines if line.startswith("SUMMARY:")), "")
    return frozenset({_collapse(summary or verdict)})


def failure_signature(verdicts: list[str]) -> frozenset[str]:
    """Normalized blocking findings of one round, comparable across rounds.

    Per failed verdict: its lines that name a FAIL (not the `STATUS:` line),
    lowercased with whitespace collapsed; the SUMMARY line when there are none.
    """
    return frozenset().union(*(_verdict_signature(item) for item in verdicts))


class StallTracker:
    """Decides after each failed round whether the loop is still making progress.

    `limit` consecutive failed rounds with the same signature stall the loop, and
    so does a fix round that changed no code. A limit of 0 turns detection off.
    """

    def __init__(self, limit: int):
        self.limit = limit
        self.signature: frozenset[str] | None = None
        self.streak = 0

    def check(
        self, number: int, failed: list[str], code_changed: bool | None
    ) -> tuple[str, str] | None:
        """`(reason code, explanation)` when round `number`'s failure is a stall."""
        if not self.limit:
            return None
        signature = failure_signature(failed)
        self.streak = self.streak + 1 if signature == self.signature else 1
        self.signature = signature
        if number > 1 and code_changed is False:
            return (
                "no_change",
                "the fix step changed no code and the round still failed",
            )
        if self.streak >= self.limit:
            return (
                "repeated_findings",
                f"the same blocking findings repeated for {self.streak} "
                "consecutive rounds",
            )
        return None
