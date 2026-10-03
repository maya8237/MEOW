"""
meow/logging.py

Structured logging setup for the harness. Every module gets its logger via
`get_logger(__name__)` and calls it with a lowercase_snake_case event name
plus keyword arguments -- never with an interpolated message string:

    logger.info("sprint_round_started", round=round_num, max_rounds=max_rounds)

`configure_logging()` wires structlog's stdlib integration once, at process
start (`cli_main`), so every logger call anywhere in the package renders
through the same key=value console output.

Set `MEOW_LOG_FILE` to also append every run's logs to a file -- the way to
get logs back from a run with no attached console, such as a Windows
Scheduled Task. See docs/INTEGRATIONS.md's "Running on a schedule" section.
"""

import logging
import os
import sys

import structlog

LOG_LEVEL_ENV_VAR = "MEOW_LOG_LEVEL"
LOG_FILE_ENV_VAR = "MEOW_LOG_FILE"

# Third-party libraries (asyncio's subprocess transport lifecycle, etc.) log
# at INFO/DEBUG too; left alone they'd interleave unstructured lines with
# meow's own key=value output. Keep them quiet unless something goes wrong.
_QUIET_LOGGERS = ["asyncio"]


def configure_logging() -> None:
    """Configure structlog for the whole process. Call once, at startup."""
    level_name = os.environ.get(LOG_LEVEL_ENV_VAR, "INFO").upper()
    level = logging.getLevelNamesMapping().get(level_name, logging.INFO)

    # Windows opens stdout/stderr with the console's codepage (cp1252/
    # "charmap") by default, not UTF-8, so structlog rendering an em-dash or
    # arrow -- as several role prompts do -- raises UnicodeEncodeError and
    # kills the run. reconfigure() falls back to `errors="replace"` instead
    # of crashing if the stream still can't represent a character. A no-op
    # on platforms where these streams are already UTF-8, and harmless if a
    # caller has replaced sys.stdout/stderr with something that doesn't
    # support reconfigure (e.g. under pytest capture).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    # stderr, not stdout: `meow run --jira` prints a single JSON result line to
    # stdout on success, which a caller (e.g. a Windows Scheduled Task) needs
    # to read cleanly without log lines interleaved into it.
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    log_file = os.environ.get(LOG_FILE_ENV_VAR)
    if log_file:
        # Appends, so repeated scheduled runs build one persistent history
        # instead of each run erasing the last one's log.
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        format="%(message)s", level=level, handlers=handlers, force=True
    )
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            # Dated, not just time-of-day: a persistent log file spans days.
            structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S"),
            # No ANSI color codes -- keeps a log file plain text.
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
