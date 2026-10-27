"""Register MEOW's one-shot triggers with the current user's OS scheduler."""

import os
import subprocess
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

_WINDOWS_TOOLS = frozenset({"schtasks", "whoami"})


def scheduler_command(argv: list[str]) -> str:
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, check=True, timeout=30
        )
    except subprocess.TimeoutExpired as exc:
        if argv[0] in _WINDOWS_TOOLS:
            from meow.execution.run_failures import WindowsQueryTimeout

            raise WindowsQueryTimeout(f"OS scheduler timed out: {argv[0]}") from exc
        raise ValueError(f"OS scheduler timed out: {argv[0]}") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        raise ValueError(f"OS scheduler failed: {detail.strip()}") from exc
    return result.stdout.strip()


def unit_dir() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    if not root.is_absolute():
        raise ValueError("XDG_CONFIG_HOME must be absolute")
    return root / "systemd" / "user"


def _unit_quote(value: str) -> str:
    # systemd performs specifier and environment expansion even inside quotes.
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("%", "%%").replace("$", "$$")
    escaped = escaped.replace("\n", "\\n").replace("\r", "\\r")
    return f'"{escaped}"'


def register_linux(  # ruff: ignore[too-many-statements]
    job: dict, directory: Path, *, replace: bool = False
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    name = f"meow-{job['id']}"
    command = " ".join(_unit_quote(arg) for arg in job["worker_argv"])
    service = directory / f"{name}.service"
    timer = directory / f"{name}.timer"
    service.write_text(
        "[Unit]\nDescription=Scheduled MEOW run\n\n[Service]\nType=oneshot\n"
        # UI attempts detach their worker. Keep it alive when this helper exits.
        f"ExecStart={command}\nTimeoutStartSec=infinity\nKillMode=process\n",
        encoding="utf-8",
    )
    at = datetime.fromisoformat(job["at"])
    timer.write_text(
        "[Unit]\nDescription=Scheduled MEOW run\n\n[Timer]\n"
        f"OnCalendar={at:%Y-%m-%d %H:%M:%S} UTC\n"
        "Persistent=true\nAccuracySec=1s\nRandomizedDelaySec=0\n\n"
        "[Install]\nWantedBy=timers.target\n",
        encoding="utf-8",
    )
    try:
        scheduler_command(["systemctl", "--user", "daemon-reload"])
        if replace:
            scheduler_command(["systemctl", "--user", "restart", timer.name])
        else:
            scheduler_command(["systemctl", "--user", "enable", "--now", timer.name])
    except ValueError:
        if replace:
            # Keep the enabled unit for the caller to restore its original time.
            raise
        # A failed enable can still have installed a symlink or started the timer.
        with suppress(ValueError):
            scheduler_command(["systemctl", "--user", "disable", "--now", timer.name])
        service.unlink(missing_ok=True)
        timer.unlink(missing_ok=True)
        raise


def register_windows(  # ruff: ignore[too-many-statements]
    job: dict, path: Path, *, replace: bool = False
) -> None:
    namespace = "http://schemas.microsoft.com/windows/2004/02/mit/task"
    ET.register_namespace("", namespace)
    task = ET.Element(f"{{{namespace}}}Task", version="1.2")
    triggers = ET.SubElement(task, "Triggers")
    trigger = ET.SubElement(triggers, "TimeTrigger")
    ET.SubElement(trigger, "StartBoundary").text = job["at"]
    ET.SubElement(trigger, "Enabled").text = "true"
    principals = ET.SubElement(task, "Principals")
    principal = ET.SubElement(principals, "Principal", id="Author")
    ET.SubElement(principal, "UserId").text = scheduler_command(["whoami"])
    ET.SubElement(principal, "LogonType").text = "InteractiveToken"
    ET.SubElement(principal, "RunLevel").text = "LeastPrivilege"
    settings = ET.SubElement(task, "Settings")
    for key, value in {
        "MultipleInstancesPolicy": "IgnoreNew",
        "DisallowStartIfOnBatteries": "false",
        "StopIfGoingOnBatteries": "false",
        "StartWhenAvailable": "true",
        "ExecutionTimeLimit": "PT0S",
    }.items():
        ET.SubElement(settings, key).text = value
    actions = ET.SubElement(task, "Actions", Context="Author")
    action = ET.SubElement(actions, "Exec")
    ET.SubElement(action, "Command").text = job["worker_argv"][0]
    ET.SubElement(action, "Arguments").text = subprocess.list2cmdline(
        job["worker_argv"][1:]
    )
    ET.SubElement(action, "WorkingDirectory").text = job["repo"]
    ET.ElementTree(task).write(path, encoding="utf-16", xml_declaration=True)
    scheduler_command([
        "schtasks",
        "/Create",
        "/TN",
        f"MEOW-{job['id']}",
        "/XML",
        str(path),
        *(["/F"] if replace else []),
    ])


def unregister(job: dict, directory: Path) -> None:
    if job["backend"] == "windows":
        scheduler_command(["schtasks", "/Delete", "/TN", f"MEOW-{job['id']}", "/F"])
        return
    name = f"meow-{job['id']}"
    # Cancel the trigger only; a run that has already started has its own controls.
    scheduler_command(["systemctl", "--user", "disable", "--now", f"{name}.timer"])
    for suffix in ("service", "timer"):
        (directory / f"{name}.{suffix}").unlink(missing_ok=True)
    scheduler_command(["systemctl", "--user", "daemon-reload"])
