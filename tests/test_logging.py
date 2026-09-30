"""Regression coverage for the Windows charmap/UnicodeEncodeError bug:
stdout/stderr default to the console codepage (cp1252) rather than UTF-8,
so structlog rendering a non-ASCII character (an arrow, an em-dash) used to
crash the run. `configure_logging()` must reconfigure both streams to UTF-8
with `errors="replace"` before wiring up the handlers."""

import io
import logging
import sys
import unittest

import structlog

from meow.logging import configure_logging, get_logger


class ConfigureLoggingEncodingTests(unittest.TestCase):
    def setUp(self):
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        self._original_root_handlers = logging.root.handlers[:]

    def tearDown(self):
        sys.stdout = self._original_stdout
        sys.stderr = self._original_stderr
        logging.root.handlers = self._original_root_handlers
        structlog.reset_defaults()

    def test_logging_a_non_ascii_message_does_not_raise_on_a_restrictive_stream(self):
        # cp1252 is what Windows gives a real process's stderr by default --
        # it cannot encode '→' ('->'), which is exactly what raised
        # UnicodeEncodeError before this fix.
        buffer = io.BytesIO()
        restrictive_stream = io.TextIOWrapper(buffer, encoding="cp1252")
        sys.stderr = restrictive_stream

        configure_logging()
        logger = get_logger("test_logging")
        logger.info("resume_at_review → generate")  # must not raise

        restrictive_stream.flush()
        self.assertIn("→".encode(), buffer.getvalue())

    def test_configure_logging_reconfigures_stdout_and_stderr_to_utf8(self):
        buffer_out, buffer_err = io.BytesIO(), io.BytesIO()
        sys.stdout = io.TextIOWrapper(buffer_out, encoding="cp1252")
        sys.stderr = io.TextIOWrapper(buffer_err, encoding="cp1252")

        configure_logging()

        self.assertEqual(sys.stdout.encoding, "utf-8")
        self.assertEqual(sys.stderr.encoding, "utf-8")

    def test_skips_streams_without_reconfigure(self):
        # A caller that replaced stdout/stderr with something that doesn't
        # support reconfigure (e.g. pytest's capture, a plain object) must
        # not break configure_logging().
        sys.stdout = object()
        sys.stderr = object()

        self.assertIsNone(configure_logging())  # must not raise
