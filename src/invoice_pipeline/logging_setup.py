"""Send the pipeline's log messages to the console and to data/logs/pipeline.log."""

import logging
import sys
from logging.handlers import RotatingFileHandler

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(filename)-17s | %(message)s"
MAX_LOG_BYTES = 2_000_000  # then a new file is started; the last 3 are kept
BACKUP_COUNT = 3


def setup_logging(config):
    """Configure the "invoice_pipeline" logger. Each script calls it once at the start.

    Every module then logs with logging.getLogger("invoice_pipeline.<module>").
    Other libraries keep their own logging.
    """
    logger = logging.getLogger("invoice_pipeline")
    logger.setLevel(config.log_level)
    logger.propagate = False

    # Remove the handlers of an earlier call (for example in a notebook),
    # otherwise every message would be printed twice.
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(LOG_FORMAT)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    logger.addHandler(console)

    config.logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = RotatingFileHandler(
        config.log_file, maxBytes=MAX_LOG_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    log_file.setFormatter(formatter)
    logger.addHandler(log_file)
