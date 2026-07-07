"""
Structured logging configuration.
"""
import logging
import os
import sys


def get_logger(name: str) -> logging.Logger:
    """Returns a configured logger with the specified name."""
    logger = logging.getLogger(name)

    if not logger.handlers:
        log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
        log_level = getattr(logging, log_level_str, logging.INFO)

        logger.setLevel(log_level)

        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(log_level)

        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        handler.setFormatter(formatter)

        logger.addHandler(handler)

    return logger
