"""Logging utilities for AI Key Pool.

Provides structured logging for key management events.
"""

import logging
import sys
from typing import Optional


_loggers: dict[str, logging.Logger] = {}


def get_logger(name: str, level: Optional[str] = None) -> logging.Logger:
    """Get or create a logger with the given name.

    Args:
        name: Logger name (typically module name)
        level: Optional log level override

    Returns:
        Configured logger instance
    """
    if name in _loggers:
        return _loggers[name]

    logger = logging.getLogger(f"aikeypool.{name}")

    if level:
        logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    else:
        logger.setLevel(logging.INFO)

    # Avoid adding duplicate handlers
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                "[%(asctime)s] %(name)s %(levelname)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            )
        )
        logger.addHandler(handler)

    _loggers[name] = logger
    return logger


def log_key_selected(logger: logging.Logger, key_id: str, provider: str) -> None:
    """Log when a key is selected for use."""
    logger.info("Key selected: %s (provider: %s)", key_id, provider)


def log_rotation(logger: logging.Logger, old_key: str, new_key: str, reason: str) -> None:
    """Log when key rotation occurs."""
    logger.warning(
        "Key rotation: %s -> %s (reason: %s)",
        old_key, new_key, reason
    )


def log_key_disabled(logger: logging.Logger, key_id: str, reason: str) -> None:
    """Log when a key is disabled."""
    logger.warning("Key disabled: %s (reason: %s)", key_id, reason)


def log_retry(logger: logging.Logger, key_id: str, attempt: int, max_retries: int) -> None:
    """Log a retry attempt."""
    logger.info("Retry %d/%d for key: %s", attempt, max_retries, key_id)


def log_no_healthy_keys(logger: logging.Logger, provider: str) -> None:
    """Log when no healthy keys are available."""
    logger.error("No healthy keys available for provider: %s", provider)


def log_request_success(logger: logging.Logger, key_id: str) -> None:
    """Log a successful request."""
    logger.debug("Request succeeded with key: %s", key_id)


def log_request_failure(logger: logging.Logger, key_id: str, error: str) -> None:
    """Log a failed request."""
    logger.warning("Request failed with key: %s - %s", key_id, error)
