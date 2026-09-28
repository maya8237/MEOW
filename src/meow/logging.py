"""
meow/logging.py

Structured logging setup for the harness. Every module gets its logger via
`get_logger(__name__)` and calls it with a lowercase_snake_case event name
plus keyword arguments -- never with an interpolated message string:

    logger.info("sprint_round_started", round=round_num, max_rounds=max_rounds)

`configure_logging()` wires structlog's stdlib integration once, at process
start (`cli_main`), so every logger call anywhere in the package renders
through the same key=value console output.
"""

import logging
import os
import sys

import structlog

LOG_LEVEL_ENV_VAR = "MEOW_LOG_LEVEL"

# Third-party libraries (asyncio's subprocess transport lifecycle, etc.) log
# at INFO/DEBUG too; left alone they'd interleave unstructured lines with
# meow's own key=value output. Keep them quiet unless something goes wrong.
_QUIET_LOGGERS = ["asyncio"]


def configure_logging() -> None:
    """Configure structlog for the whole process. Call once, at startup."""
    level_name = os.environ.get(LOG_LEVEL_ENV_VAR, "INFO").upper()
    level = logging.getLevelNamesMapping().get(level_name, logging.INFO)

    logging.basicConfig(
        format="%(message)s", stream=sys.stdout, level=level, force=True
    )
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
