"""Unit tests for schedule generator config helpers."""

from __future__ import annotations

import logging

from neuralcast.pipelines.schedule_generator import config


def test_configure_logging_is_idempotent(monkeypatch) -> None:
    logger = logging.getLogger("schedule_generator")
    original_handlers = list(logger.handlers)
    for handler in original_handlers:
        logger.removeHandler(handler)

    try:
        config.configure_logging()
        first_handlers = list(logger.handlers)
        config.configure_logging()
        assert logger.handlers == first_handlers
        assert logger.propagate is False
    finally:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
        for handler in original_handlers:
            logger.addHandler(handler)
