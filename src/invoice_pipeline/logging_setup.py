"""Logging setup: one call at startup, then every module uses its own logger."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from invoice_pipeline.config import Config

LOGGER_NAME = "invoice_pipeline"
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
MAX_LOG_BYTES = 2_000_000
BACKUP_COUNT = 3


def setup_logging(config: Config) -> logging.Logger:
    """Configure the package logger to write to the console and to a file.

    Only the "invoice_pipeline" logger is configured, not the root logger, so
    third-party libraries keep their own logging behavior. Safe to call more
    than once: existing handlers are replaced instead of duplicated.

    Modules should not call this; they just do::

        logger = logging.getLogger(__name__)
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(config.log_level)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(LOG_FORMAT)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    logger.addHandler(console)

    config.logs_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        config.log_file,
        maxBytes=MAX_LOG_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
