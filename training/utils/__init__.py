"""Training utilities and helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = [
    "AzureConfigError",
    "AzureMLContext",
    "SystemMetricsCollector",
    "bootstrap_azure_ml",
    "require_env",
    "set_env_defaults",
]

_EXPORTS = {
    "AzureConfigError": "training.utils.context",
    "AzureMLContext": "training.utils.context",
    "bootstrap_azure_ml": "training.utils.context",
    "require_env": "training.utils.env",
    "set_env_defaults": "training.utils.env",
    "SystemMetricsCollector": "training.utils.metrics",
}


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module), name)


if TYPE_CHECKING:
    from training.utils.context import AzureConfigError, AzureMLContext, bootstrap_azure_ml
    from training.utils.env import require_env, set_env_defaults
    from training.utils.metrics import SystemMetricsCollector
