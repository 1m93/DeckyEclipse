"""Logger indirection so modules work both inside Decky and in the launcher."""

import logging

_logger = logging.getLogger("eclipse")


def set_logger(logger: logging.Logger) -> None:
    """Route all module logging through ``logger`` (the Decky plugin logger)."""
    global _logger
    _logger = logger


def get_logger() -> logging.Logger:
    return _logger
