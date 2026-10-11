"""Exploratory tester role and project architecture context."""

from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions

from meow.agents.base import Agent
from meow.agents.reviewer import _verdict_status, force_fail
from meow.infrastructure.test_runner import VerificationStageEvidence
from meow.project.plan_files import plan_test_file
from meow.project.prompts import tester_prompt


def architecture_context(
    active_dir: Path, explicit_files: tuple[Path, ...] | list[Path]
) -> str:
    """Read the preferred architecture doc plus explicitly configured docs."""
    root = active_dir.resolve()
    preferred = root / "docs" / "ARCHITECTURE.md"
    sources = [preferred]
    sources.extend(root / path for path in explicit_files)
    sections = []
    seen = set()
    for path in sources:
        resolved = path.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                f"architecture file {path} escapes the active worktree"
            ) from exc
        if resolved in seen:
            continue
        seen.add(resolved)
        if not resolved.is_file():
            continue
        sections.append(
            f"## {resolved.relative_to(root).as_posix()}\n"
            f"{resolved.read_text(encoding='utf-8')}"
        )
    if not sections:
        return (
            "No architecture document is available; proceed using repository evidence."
        )
    return "\n\n".join(sections)


class VerificationAgent(Agent):
    """Tests a reviewed plan using configured evidence and normal tools."""

    def _mcp_servers(self) -> dict:
        return {
            entry["name"]: {
                "command": entry["command"],
                "args": entry["args"],
                **({"env": entry["env"]} if entry["env"] else {}),
            }
            for entry in self.context.config.get("tester", {}).get("mcp", [])
        }

    def _options(self, report_file: Path) -> ClaudeAgentOptions:
        extra = {}
        servers = self._mcp_servers()
        if servers:
            extra["mcp_servers"] = servers
        allowed_tools = ["Read", "Grep", "Glob", "Bash", "Write"]
        allowed_tools.extend(f"mcp__{name}__*" for name in servers)
        return self.options(
            system_prompt=tester_prompt(report_file),
            allowed_tools=allowed_tools,
            role="tester",
            **extra,
        )

    async def test_plan(  # ruff: ignore[too-many-statements, complex-structure, too-many-branches]
        self, plan_file: Path, evidence: VerificationStageEvidence
    ) -> tuple[str, str]:
        report_file = plan_test_file(plan_file)
        active_dir = self.context.active_working_dir()
        try:
            plan = plan_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise RuntimeError(
                f"Could not read tester plan {plan_file}: {exc}"
            ) from exc
        tester_config = self.context.config.get("tester", {})
        architecture = architecture_context(
            active_dir, tester_config.get("architecture_files", [])
        )
        results = []
        for command in evidence.commands:
            status = (
                "timed out" if command.timed_out else f"exit code {command.exit_code}"
            )
            results.append(
                f"cwd: {command.cwd}\ncommand: {command.command}\n"
                f"gate: {command.gate}\nresult: {status}\noutput:\n"
                f"{command.output or '(no output)'}"
            )
        configured_dirs = tester_config.get("test_dirs", [])
        base_urls = [*evidence.server_urls]
        if tester_config.get("base_url"):
            base_urls.append(tester_config["base_url"])
        browser = (
            "\n".join(
                f"provider: {item.provider} ({item.kind})\nstatus: {item.status}\n"
                f"required: {item.required}\nreason: {item.reason}\n"
                f"flows: {', '.join(item.flows) or '(none)'}\n"
                f"artifacts: {', '.join(item.artifacts) or '(none)'}\n"
                f"output:\n{item.output}"
                for item in evidence.browser
            )
            or "(no browser provider evidence)"
        )
        prompt = (
            f"Plan file: {plan_file}\nPlan content:\n{plan}\n\n"
            f"Active worktree: {active_dir}\n\nArchitecture context:\n"
            f"{architecture}\n\n"
            f"Configured test directories: "
            f"{', '.join(map(str, configured_dirs)) or '(none)'}\n"
            f"Base URLs: {', '.join(base_urls) or '(none)'}\n\n"
            f"Browser provider results:\n{browser}\n\n"
            "Configured test command results:\n"
            + ("\n\n".join(results) if results else "(no configured test commands)")
        )
        try:
            previous_report = report_file.read_bytes()
        except FileNotFoundError:
            previous_report = None
        except OSError as exc:
            return "FAIL", f"Could not prepare tester verdict at {report_file}: {exc}"
        try:
            report_file.unlink(missing_ok=True)
            await self.run_query(prompt, self._options(report_file), "Tester")
        except BaseException:
            if previous_report is not None and not report_file.exists():
                report_file.write_bytes(previous_report)
            raise
        try:
            verdict = report_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            if previous_report is not None and not report_file.exists():
                report_file.write_bytes(previous_report)
            return (
                "FAIL",
                f"SUMMARY: Tester did not produce a readable verdict at "
                f"{report_file}.\nSTATUS: FAIL",
            )
        status = _verdict_status(verdict)
        if evidence.blocking_failed:
            failures = [
                f"{command.command} "
                f"({'timeout' if command.timed_out else command.exit_code})"
                for command in evidence.commands
                if command.gate
                and (command.timed_out or command.exit_code not in {0, None})
            ]
            verdict = force_fail(
                verdict, f"Mandatory test command failed: {'; '.join(failures)}."
            )
            report_file.write_text(verdict, encoding="utf-8")
            return "FAIL", verdict
        return status, verdict
