"""The trainer's logger: the console keeps the old look, the file keeps everything.

The whole stack used to talk in print() - which means a training night left no
trace of its own decisions (why a resume was refused, what speed the mod actually
applied, when a pause was not confirmed) except scattered in a closed console.
setup_logging() attaches two handlers to the "hk_train" logger: a console one that
formats the message exactly as print did (so the console and the regex-verified
output do not change) and a file one that adds timestamps and appends across runs.

Imports nothing but the standard library, so the unit tier checks it directly.
"""

import logging


LOGGER_NAME = "hk_train"


def get_logger():
    return logging.getLogger(LOGGER_NAME)


def setup_logging(path, level=logging.INFO):
    """Configures the trainer logger with a console and a file handler.

    Idempotent on purpose: a repeated learn() call in the same process must not
    add a second pair of handlers (doubled console lines) or reopen the file.
    Returns the configured logger.
    """
    logger = logging.getLogger(LOGGER_NAME)
    if getattr(logger, "_hk_configured", False):
        return logger
    logger.setLevel(level)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(console)
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(file_handler)
    logger.propagate = False
    logger._hk_configured = True
    return logger
