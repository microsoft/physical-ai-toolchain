"""Environment variable helpers for training workflows."""

from __future__ import annotations

import os
from collections.abc import Mapping


def require_env(name: str, *, error_type: type[Exception] = RuntimeError) -> str:
    """Return the value of an environment variable or raise when missing.

    Both unset and empty-string values are treated as missing.

    Args:
        name: Environment variable name to look up.
        error_type: Exception class to raise on missing value.

    Returns:
        Non-empty string value of the environment variable.

    Raises:
        error_type: When *name* is unset or empty.
    """
    value = os.environ.get(name)
    if not value:
        raise error_type(f"Environment variable {name} is required for Azure ML bootstrap")
    return value


def set_env_defaults(defaults: Mapping[str, str]) -> None:
    """Populate environment variables with defaults for keys not already set.

    Existing values are preserved. Only missing keys receive the
    provided default via ``os.environ.setdefault``.

    Args:
        defaults: Mapping of variable names to default string values.
    """
    for key, default_value in defaults.items():
        os.environ.setdefault(key, default_value)
