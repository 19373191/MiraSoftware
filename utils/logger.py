"""
M.I.R.A. Centralized Logger Utility.
"""

import logging
import sys
from config import settings


def setup_logger(name: str = "MIRA") -> logging.Logger:
    """
    Configures and returns a structured logger instance for the application.

    Args:
        name: The name of the logger module.

    Returns:
        logging.Logger: Configured logger instance.
    """
    logger = logging.getLogger(name)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logger.setLevel(log_level)

    return logger


logger = setup_logger("MIRA")
