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

from meow.infrastructure.logging import configure_logging, get_logger


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


def _reset_error_log():
    from meow.infrastructure import logging as meow_logging

    meow_logging._reset_error_log_for_tests()


def test_error_log_survives_kill(tmp_path):
    import subprocess
    import textwrap

    path = tmp_path / "run.error.log"
    code = textwrap.dedent(
        f"""
        import os
        from pathlib import Path
        from meow.infrastructure.logging import (
            configure_error_log, configure_logging, get_logger,
        )
        configure_logging()
        configure_error_log(Path({str(path)!r}))
        get_logger("t").error("last_words")
        os._exit(1)
        """
    )
    result = subprocess.run([sys.executable, "-c", code], check=False)
    assert result.returncode == 1
    assert "last_words" in path.read_text(encoding="utf-8")


def test_configure_error_log_is_idempotent_and_keeps_faulthandler_file(tmp_path):
    import faulthandler

    from meow.infrastructure import logging as meow_logging

    saved = logging.root.handlers[:]
    try:
        path = tmp_path / "a.error.log"
        meow_logging.configure_error_log(path)
        meow_logging.configure_error_log(path)
        ours = [
            h for h in logging.root.handlers if isinstance(h, meow_logging.FsyncHandler)
        ]
        assert len(ours) == 1
        assert faulthandler.is_enabled()
        assert not meow_logging._fault_file.closed
        meow_logging.configure_error_log(tmp_path / "b.error.log")
        assert (
            len([
                h
                for h in logging.root.handlers
                if isinstance(h, meow_logging.FsyncHandler)
            ])
            == 1
        )
    finally:
        _reset_error_log()
        logging.root.handlers = saved


def test_error_log_ignores_info(tmp_path):
    from meow.infrastructure import logging as meow_logging

    saved = logging.root.handlers[:]
    try:
        meow_logging.configure_error_log(tmp_path / "c.error.log")
        logging.getLogger("x").setLevel(logging.INFO)
        logging.getLogger("x").info("noise")
        logging.getLogger("x").warning("signal")
        text = (tmp_path / "c.error.log").read_text(encoding="utf-8")
        assert "signal" in text
        assert "noise" not in text
    finally:
        _reset_error_log()
        logging.root.handlers = saved


def test_meow_log_file_handler_flushes_every_record(tmp_path, monkeypatch):
    target = tmp_path / "meow.log"
    monkeypatch.setenv("MEOW_LOG_FILE", str(target))
    saved = logging.root.handlers[:]
    try:
        configure_logging()
        get_logger("t").info("flushed_event")
        # Read while the handler is still open: nothing buffered.
        assert "flushed_event" in target.read_text(encoding="utf-8")
    finally:
        for handler in logging.root.handlers:
            handler.close()
        logging.root.handlers = saved
        structlog.reset_defaults()
