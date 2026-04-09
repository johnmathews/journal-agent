"""Tests for logging setup."""

import logging

from journal.logging import setup_logging


class TestSetupLogging:
    def setup_method(self):
        """Clear handlers before each test."""
        logger = logging.getLogger("journal")
        logger.handlers.clear()

    def test_adds_handler_on_first_call(self):
        setup_logging()
        logger = logging.getLogger("journal")
        assert len(logger.handlers) == 1

    def test_idempotent_on_repeated_calls(self):
        setup_logging()
        setup_logging()
        setup_logging()
        logger = logging.getLogger("journal")
        assert len(logger.handlers) == 1

    def test_sets_level(self):
        setup_logging("DEBUG")
        logger = logging.getLogger("journal")
        assert logger.level == logging.DEBUG

    def test_updates_level_without_adding_handler(self):
        setup_logging("INFO")
        setup_logging("DEBUG")
        logger = logging.getLogger("journal")
        assert logger.level == logging.DEBUG
        assert len(logger.handlers) == 1

    def test_quiets_noisy_libraries(self):
        setup_logging()
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("httpcore").level == logging.WARNING
        assert logging.getLogger("openai").level == logging.WARNING
        assert logging.getLogger("anthropic").level == logging.WARNING
