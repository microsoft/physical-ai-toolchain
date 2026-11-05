"""Shared logging thresholds for the API and standalone release worker."""

from __future__ import annotations

import logging
import os

_LEVELS = {
    "critical": logging.CRITICAL,
    "error": logging.ERROR,
    "warning": logging.WARNING,
    "info": logging.INFO,
    "debug": logging.DEBUG,
    "trace": 5,
}


def _level(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return _LEVELS[value.strip().lower()]
    except KeyError:
        raise ValueError(f"{name} must be critical, error, warning, info, debug, or trace") from None


def configure_logging(default_level: int = logging.INFO) -> None:
    """Apply validated environment levels after dotenv loading, preserving handlers."""
    app_level = _level("LOG_LEVEL", default_level)
    azure_level = _level("AZURE_LOG_LEVEL", logging.WARNING)
    logging.basicConfig(level=app_level, format="%(asctime)s - %(levelname)s %(name)s - %(message)s")
    logging.getLogger().setLevel(app_level)
    logging.getLogger("src.api").setLevel(app_level)
    logging.getLogger("release_worker").setLevel(app_level)
    logging.getLogger("azure").setLevel(azure_level)
    logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(azure_level)
